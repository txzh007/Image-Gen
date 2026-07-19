#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible 异步视频生成 CLI（零第三方依赖）。"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MODEL = "video-ds-2.0-fast"
DEFAULT_ENDPOINT = "/videos"
TERMINAL_FAILURE_STATES = {"failed", "cancelled", "canceled", "expired"}


def join_url(base, endpoint):
    base = base.rstrip("/")
    endpoint = endpoint or DEFAULT_ENDPOINT
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base + endpoint


def deep_merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_param_assignments(assignments):
    result = {}
    for assignment in assignments or []:
        if "=" not in assignment:
            raise ValueError(f"--param 必须是 key=value：{assignment}")
        dotted_key, raw_value = assignment.split("=", 1)
        keys = [part.strip() for part in dotted_key.split(".") if part.strip()]
        if not keys:
            raise ValueError(f"--param 的 key 不能为空：{assignment}")
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value

        cursor = result
        for key in keys[:-1]:
            existing = cursor.get(key)
            if existing is None:
                cursor[key] = {}
            elif not isinstance(existing, dict):
                raise ValueError(f"--param 路径冲突：{dotted_key}")
            cursor = cursor[key]
        cursor[keys[-1]] = value
    return result


def validate_reference_urls(kind, values):
    for value in values or []:
        if not re.match(r"^https?://", value, re.I):
            raise ValueError(
                f"--{kind} 只接受公网 http/https URL，不能直接传本地路径：{value}"
            )


def build_video_body(
    model,
    prompt,
    seconds="5",
    aspect_ratio="16:9",
    images=None,
    videos=None,
    audios=None,
    extra_body=None,
):
    if not prompt:
        raise ValueError("创建视频任务时必须提供提示词。")
    validate_reference_urls("image", images)
    validate_reference_urls("video", videos)
    validate_reference_urls("audio", audios)

    body = {
        "model": model,
        "prompt": prompt,
        "seconds": str(seconds),
        "aspect_ratio": aspect_ratio,
    }
    if images:
        body["images"] = list(images)
    if videos:
        body["videos"] = list(videos)
    if audios:
        body["audios"] = list(audios)
    return deep_merge(extra_body or {}, body)


def build_headers(api_key, json_content=True):
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if json_content:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def setup_proxy(no_proxy):
    if no_proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        urllib.request.install_opener(opener)


def http_json(method, url, headers, body=None, timeout=60):
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = None
    return status, parsed, raw


def error_message(status, parsed, raw):
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code")
            if detail:
                return f"HTTP {status}: {detail}"
        detail = parsed.get("message") or parsed.get("code")
        if detail:
            return f"HTTP {status}: {detail}"
    return f"HTTP {status}: {raw.decode('utf-8', 'replace')[:500]}"


def extract_task_id(payload):
    if not isinstance(payload, dict):
        return None
    return payload.get("task_id") or payload.get("id")


def extract_video_url(payload):
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict):
        value = result.get("video_url")
        if value:
            return value
        urls = result.get("resultUrls") or result.get("result_urls")
        if isinstance(urls, list) and urls:
            return urls[0]
    value = payload.get("video_url") or payload.get("url")
    return value if isinstance(value, str) else None


def sanitize(payload, limit=240):
    if isinstance(payload, dict):
        return {key: sanitize(value, limit) for key, value in payload.items()}
    if isinstance(payload, list):
        return [sanitize(value, limit) for value in payload]
    if isinstance(payload, str) and len(payload) > limit:
        return payload[:limit] + f"...<+{len(payload) - limit} chars>"
    return payload


def wait_for_task(base_url, endpoint, task_id, headers, args):
    task_url = f"{join_url(base_url, endpoint)}/{task_id}"
    deadline = time.monotonic() + args.wait_timeout
    last_summary = None

    while True:
        status, payload, raw = http_json(
            "GET", task_url, headers, timeout=args.request_timeout
        )
        if status >= 300 or payload is None:
            raise RuntimeError(error_message(status, payload, raw))
        state = str(payload.get("status", "unknown")).lower()
        progress = payload.get("progress")
        summary = (state, progress)
        if summary != last_summary:
            print(f"[video] 状态={state}, 进度={progress if progress is not None else '?'}%")
            last_summary = summary
        if args.debug:
            print(json.dumps(sanitize(payload), ensure_ascii=False, indent=2))

        if state == "completed":
            return payload
        if state in TERMINAL_FAILURE_STATES:
            detail = payload.get("error") or payload.get("message") or "未知错误"
            raise RuntimeError(f"视频任务 {state}: {detail}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待超过 {args.wait_timeout} 秒；任务仍可续查："
                f"python3 genvideo.py --task-id {task_id}"
            )
        time.sleep(args.poll_interval)


def _looks_like_mp4(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
        return b"ftyp" in head
    except OSError:
        return False


def download_to_path(url, output, headers, timeout):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + ".part")
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with open(part, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise

    if not _looks_like_mp4(part):
        try:
            snippet = part.read_text(encoding="utf-8", errors="replace")[:200]
        except OSError:
            snippet = ""
        part.unlink(missing_ok=True)
        raise RuntimeError(f"下载结果不是 MP4：{snippet}")
    part.replace(output)
    return output, output.stat().st_size


def download_completed_video(base_url, endpoint, task_id, payload, output, api_key, timeout):
    content_url = f"{join_url(base_url, endpoint)}/{task_id}/content"
    try:
        return download_to_path(
            content_url,
            output,
            build_headers(api_key, json_content=False),
            timeout,
        )
    except Exception as error:
        fallback_url = extract_video_url(payload)
        if not fallback_url:
            raise RuntimeError(f"/content 下载失败，且任务没有返回备用 URL：{error}") from error
        print(f"[video] ⚠ /content 下载失败，改用任务返回的临时 URL：{error}")
        # 不把中转站 token 转发给第三方对象存储。
        return download_to_path(fallback_url, output, {}, timeout)


def default_output_path():
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("output") / f"video_{stamp}.mp4"


def create_parser():
    parser = argparse.ArgumentParser(
        prog="genvideo",
        description="异步视频生成 CLI：创建任务、轮询状态并下载 MP4。",
    )
    parser.add_argument("prompt", nargs="?", help="视频提示词；使用 --task-id 时可省略")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"视频模型，默认 {DEFAULT_MODEL}")
    parser.add_argument("--seconds", default="5", help="视频时长，例如 5、10、15")
    parser.add_argument("--aspect-ratio", default="16:9",
                        help="画面比例，例如 16:9、9:16、1:1")
    parser.add_argument("--image", action="append", default=[],
                        help="公网参考图片 URL，可重复；不能直接传本地路径")
    parser.add_argument("--video", action="append", default=[],
                        help="公网参考视频 URL，可重复；不能直接传本地路径")
    parser.add_argument("--audio", action="append", default=[],
                        help="公网参考音频 URL，可重复；不能直接传本地路径")
    parser.add_argument("--task-id", help="续查已有任务，不创建新任务")
    parser.add_argument("--no-wait", action="store_true",
                        help="创建后立即返回 task_id，不轮询或下载")
    parser.add_argument("--out", "-o", help="输出 MP4 路径")
    parser.add_argument("--base-url", help="覆盖 IMAGE_API_BASE")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"视频任务路径，默认 {DEFAULT_ENDPOINT}")
    parser.add_argument("--api-key", help="覆盖 API key（不建议，可能进入命令历史）")
    parser.add_argument("--poll-interval", type=int, default=15,
                        help="轮询间隔秒数，默认 15")
    parser.add_argument("--wait-timeout", type=int, default=900,
                        help="总等待上限秒数，默认 900")
    parser.add_argument("--request-timeout", type=int, default=120,
                        help="单次 HTTP 请求超时秒数，默认 120")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                        help="追加/覆盖请求 JSON 字段，可重复，支持 a.b=value")
    parser.add_argument("--no-proxy", action="store_true", help="忽略系统代理，强制直连")
    parser.add_argument("--debug", action="store_true", help="打印完整任务响应")
    parser.add_argument("--dry-run", action="store_true", help="打印创建请求但不联网")
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.poll_interval < 1:
        parser.error("--poll-interval 必须大于 0")
    if args.wait_timeout < 1 or args.request_timeout < 1:
        parser.error("超时参数必须大于 0")
    if args.task_id and args.dry_run:
        parser.error("--task-id 不能与 --dry-run 一起使用")
    if not args.task_id and not args.prompt:
        parser.error("创建任务时缺少视频提示词")

    base_url = args.base_url or os.environ.get("IMAGE_API_BASE")
    api_key = (
        args.api_key
        or os.environ.get("GENIMG_API_KEY")
        or os.environ.get("IMAGE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not base_url:
        parser.error("缺少 IMAGE_API_BASE，或使用 --base-url 指定")
    setup_proxy(args.no_proxy)
    headers = build_headers(api_key)

    task_id = args.task_id
    if not task_id:
        try:
            param_body = parse_param_assignments(args.param)
            body = build_video_body(
                args.model,
                args.prompt,
                seconds=args.seconds,
                aspect_ratio=args.aspect_ratio,
                images=args.image,
                videos=args.video,
                audios=args.audio,
            )
            body = deep_merge(body, param_body)
        except ValueError as error:
            parser.error(str(error))
        create_url = join_url(base_url, args.endpoint)
        print(f"[video] POST {create_url} (model={args.model}, seconds={args.seconds})")
        if args.debug or args.dry_run:
            print(json.dumps(sanitize(body), ensure_ascii=False, indent=2))
        if args.dry_run:
            print("[video] ✓ dry-run，未创建付费任务")
            return 0

        status, payload, raw = http_json(
            "POST", create_url, headers, body=body, timeout=args.request_timeout
        )
        if status >= 300 or payload is None:
            print(f"[video] ✗ 创建失败：{error_message(status, payload, raw)}")
            return 1
        task_id = extract_task_id(payload)
        if not task_id:
            print("[video] ✗ 返回中没有 task_id/id")
            if args.debug:
                print(json.dumps(sanitize(payload), ensure_ascii=False, indent=2))
            return 1
        print(f"[video] ✓ 任务已创建：{task_id}")
        if args.no_wait:
            print(f"续查：python3 genvideo.py --task-id {task_id}")
            return 0
    else:
        print(f"[video] 续查任务：{task_id}")

    try:
        payload = wait_for_task(base_url, args.endpoint, task_id, headers, args)
        output = Path(args.out) if args.out else default_output_path()
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
        path, size = download_completed_video(
            base_url,
            args.endpoint,
            task_id,
            payload,
            output,
            api_key,
            args.request_timeout,
        )
        print(f"[video] ✓ 保存 {path} ({size // 1024} KB)")
        return 0
    except KeyboardInterrupt:
        print(f"\n[video] 已停止等待，任务仍可续查：python3 genvideo.py --task-id {task_id}")
        return 130
    except Exception as error:
        print(f"[video] ✗ {error}")
        print(f"任务 ID：{task_id}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
