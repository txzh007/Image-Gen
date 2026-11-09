#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible 异步视频生成 CLI（零第三方依赖）。"""

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MODEL = "video-ds-2.0-fast"
DEFAULT_OPENAI_VIDEO_MODEL = "sora-2"
PROTOCOL_SUB2 = "sub2"      # Sub2API / 即梦风格扩展字段（aspect_ratio、images[]）
PROTOCOL_OPENAI = "openai"  # 严格 OpenAI Videos API（size、input_reference、4/8/12 秒）
# OpenAI 只认这几个尺寸，比例必须换算成具体宽高。
OPENAI_VIDEO_SIZES = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "3:2": "1792x1024",
    "2:3": "1024x1792",
}
OPENAI_VIDEO_SIZE_SET = {"720x1280", "1280x720", "1024x1792", "1792x1024"}
OPENAI_VIDEO_SECONDS = ("4", "8", "12")
DEFAULT_ENDPOINT = "/videos"
TERMINAL_FAILURE_STATES = {"failed", "cancelled", "canceled", "expired"}
# 不同中转/上游对「成功」的叫法不一样：官方 OpenAI 用 completed，
# xAI/Grok 媒体渠道返回 done，其它还有 succeeded/finished/ready 等。
SUCCESS_STATES = {"completed", "done", "succeeded", "success", "finished", "ready"}
TERMINAL_STATES = TERMINAL_FAILURE_STATES | SUCCESS_STATES
TASK_LOG_NAME = ".genvideo-tasks.jsonl"
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_DELAYS = (2, 4)


def configure_stdio():
    """统一 stdout/stderr 编码。

    Windows 控制台默认按本地代码页（cp936/cp1252）解码，输出里的中文和
    ✓ ✗ ⚠ → 轻则乱码、重则 UnicodeEncodeError 直接崩。这里统一改成 UTF-8
    并对无法编码的字符降级替换，保证任何终端下都不中断。
    """
    original = (getattr(sys.stdout, "encoding", "") or "").lower()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (LookupError, ValueError, OSError):
            continue
    if original and original.replace("-", "") not in ("utf8", "utf8mb4"):
        if sys.platform.startswith("win"):
            print(
                "[提示] Windows 终端若显示乱码，先执行 chcp 65001，"
                "或改用 Windows Terminal / PowerShell 7。",
                file=sys.stderr,
            )


VIDEO_MODEL_HINTS = (
    "video", "seedance", "veo", "sora", "kling", "hailuo", "movie", "animate",
    "ds-2.0", "wan2", "wanx", "cogvideo", "minimax-video",
)
NON_VIDEO_MODEL_HINTS = (
    "image", "dall", "flux", "banana", "embedding", "embed", "tts", "whisper",
    "rerank", "moderation", "transcribe", "asr", "ocr",
)


def looks_like_video_model(name):
    """按名字猜是否视频模型；中转的 /models 不带能力字段，只能启发式。"""
    lowered = str(name).lower()
    if any(bad in lowered for bad in NON_VIDEO_MODEL_HINTS):
        return False
    return any(hint in lowered for hint in VIDEO_MODEL_HINTS)


def fetch_models(base_url, api_key, timeout=20):
    """GET /models，返回 (names, error)；失败不抛异常。"""
    request = urllib.request.Request(
        join_url(base_url, "/models"),
        headers=build_headers(api_key),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return None, f"HTTP {error.code}"
    except urllib.error.URLError as error:
        return None, str(error.reason)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "返回不是 JSON"
    items = payload.get("data") or payload.get("models") or []
    if isinstance(items, dict):
        items = list(items.values())
    names, seen = [], set()
    for item in items:
        name = item.get("id") or item.get("name") if isinstance(item, dict) else str(item)
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            names.append(name)
    return names, None


def print_video_models(names, error=None):
    if names is None:
        print(f"拉取模型列表失败：{error}")
        print("可以直接用 --model NAME 指定视频模型，或检查 IMAGE_API_BASE / GENIMG_API_KEY。")
        return
    videos = [n for n in names if looks_like_video_model(n)]
    print(f"账号可用模型 {len(names)} 个，其中疑似视频模型 {len(videos)} 个：")
    for index, name in enumerate(videos, 1):
        print(f"  {index}) {name}")
    if not videos:
        print(
            "  （没有视频模型）该账号分组未开通视频能力，需要中转后台授权后再用；"
            "图片能力不受影响。"
        )
    others = [n for n in names if n not in videos]
    if others:
        shown = ", ".join(others[:10]) + ("…" if len(others) > 10 else "")
        print(f"其它 {len(others)} 个（按名字判断不是视频模型）：{shown}")


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


def validate_reference_urls(kind, values, auto_upload=None):
    """验证参考素材 URL，可选自动上传本地文件"""
    processed = []
    for value in values or []:
        if re.match(r"^https?://", value, re.I):
            # 已经是 URL，直接使用
            processed.append(value)
        elif os.path.exists(value):
            # 本地文件路径
            if auto_upload:
                print(f"[upload] 检测到本地文件 {value}，正在上传到 {auto_upload}...", file=sys.stderr)
                try:
                    from upload import upload_file
                    upload_api_key = None
                    if auto_upload == "imgbb":
                        upload_api_key = os.environ.get("IMGBB_API_KEY")
                    elif auto_upload == "smms":
                        upload_api_key = os.environ.get("SMMS_API_KEY")
                    url = upload_file(value, auto_upload, upload_api_key)
                    print(f"[upload] ✓ 上传成功: {url}", file=sys.stderr)
                    processed.append(url)
                except Exception as e:
                    raise ValueError(f"自动上传 {value} 失败: {e}")
            else:
                raise ValueError(
                    f"--{kind} 检测到本地文件路径 {value}，但视频任务需要公网 URL。\n"
                    f"解决方案：\n"
                    f"  1. 使用 --auto-upload 自动上传: --auto-upload catbox\n"
                    f"  2. 手动上传: python3 upload.py {value}\n"
                    f"  3. 使用已有的公网 URL"
                )
        else:
            raise ValueError(
                f"--{kind} 参数无效（既不是 URL 也不是本地文件）: {value}；"
                "视频参考素材必须使用公网 URL。"
            )
    return processed


def build_openai_video_body(
    model, prompt, seconds="4", aspect_ratio="16:9", size=None,
    images=None, videos=None, audios=None, extra_body=None, auto_upload=None,
):
    """严格 OpenAI Videos API 请求体：size + input_reference，秒数只认 4/8/12。"""
    if not prompt:
        raise ValueError("创建视频任务时必须提供提示词。")
    if videos or audios:
        raise ValueError(
            "OpenAI 视频协议只接受一张 input_reference 参考图，不支持视频/音频参考；"
            "需要这些素材请用 --protocol sub2。"
        )
    second_text = str(seconds)
    if second_text not in OPENAI_VIDEO_SECONDS:
        raise ValueError(
            f"OpenAI 视频协议只支持 { '/'.join(OPENAI_VIDEO_SECONDS) } 秒，"
            f"当前 {seconds} 秒；要么改 --seconds，要么用 --protocol sub2。"
        )
    if size:
        if size not in OPENAI_VIDEO_SIZE_SET:
            raise ValueError(
                f"OpenAI 视频协议 size 只支持 {', '.join(sorted(OPENAI_VIDEO_SIZE_SET))}，"
                f"当前 {size}。"
            )
        resolved_size = size
    else:
        resolved_size = OPENAI_VIDEO_SIZES.get(str(aspect_ratio))
        if not resolved_size:
            raise ValueError(
                f"OpenAI 视频协议没有 {aspect_ratio} 这个比例；"
                f"可用比例 {', '.join(OPENAI_VIDEO_SIZES)}，或直接用 --size 指定宽高。"
            )
    image_urls = validate_reference_urls("image", images, auto_upload)
    if len(image_urls) > 1:
        raise ValueError(
            f"OpenAI 视频协议只接受一张参考图（input_reference），当前给了 {len(image_urls)} 张。"
        )
    body = {
        "model": model or DEFAULT_OPENAI_VIDEO_MODEL,
        "prompt": prompt,
        "seconds": second_text,
        "size": resolved_size,
    }
    if image_urls:
        body["input_reference"] = image_urls[0]
    return deep_merge(extra_body or {}, body)


def build_video_body(
    model,
    prompt,
    seconds="5",
    aspect_ratio="16:9",
    images=None,
    videos=None,
    audios=None,
    extra_body=None,
    auto_upload=None,
    duration=None,
    resolution=None,
):
    if not prompt:
        raise ValueError("创建视频任务时必须提供提示词。")
    images = validate_reference_urls("image", images, auto_upload)
    videos = validate_reference_urls("video", videos, auto_upload)
    audios = validate_reference_urls("audio", audios, auto_upload)

    body = {"model": model, "prompt": prompt}
    # 两种字段风格二选一：duration/resolution（数字时长 + 具名分辨率）
    # 或 seconds/aspect_ratio。实测 duration+resolution 会直接改变输出分辨率与计费。
    if duration is not None:
        body["duration"] = int(duration)
    else:
        body["seconds"] = str(seconds)
    if resolution:
        body["resolution"] = resolution
    else:
        body["aspect_ratio"] = aspect_ratio
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


def explain_create_error(status, parsed, raw):
    """创建失败时的针对性诊断。

    视频任务创建刻意不自动重试 502/504：上游可能已经建好任务只是响应丢了，
    盲目重试会产生第二个付费任务。
    """
    message = ""
    if isinstance(parsed, dict):
        error = parsed.get("error") or parsed.get("message") or ""
        message = str(error if not isinstance(error, dict) else error.get("message") or error)
    lowered = message.lower()
    if "no eligible" in lowered or "not supported by any configured account" in lowered:
        return (
            "中转分组没有可用的上游账号（No eligible …accounts）：渠道侧配置问题，与 Key 无关。"
            "需要中转管理员为该分组接入上游账号，再重试；不要反复重试。"
        )
    if status in (502, 504):
        return (
            f"网关 {status}：中转上游没有正常响应。任务通常没建成，但也可能已建成而响应丢失——"
            "先 `--list-tasks` 看本地记录、到中转后台核对用量，再手动重试，避免重复付费。"
        )
    if status == 429:
        return "限流：稍等再试，不要连续重试。"
    if status in (401, 403):
        return "鉴权失败：检查 GENIMG_API_KEY 是否有效、是否有该模型权限。"
    if status in (400, 422):
        if "model_not_found" in lowered or "not available" in lowered:
            return "该模型未对当前分组开放：先跑 `--list-models` 看账号有哪些视频模型。"
        if "upstream returned status" in lowered or "invalid" in lowered:
            return (
                "上游拒绝了请求参数（400/422）：多数是 resolution 档位超出模型能力。"
                "实测 grok-imagine-video 只支持 480p/720p，1080p 需要 grok-imagine-video-1.5；"
                "并且 resolution 只认 480p/720p/1080p 这类档位名，写 1920x1080 会被拒。"
            )
    return None


# 不同中转给任务标识起的名字不一样，按优先级依次尝试。
TASK_ID_FIELDS = ("task_id", "request_id", "video_id", "job_id", "taskId", "id")


def extract_task_id(payload):
    if not isinstance(payload, dict):
        return None
    for field in TASK_ID_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for wrapper in ("data", "result", "task", "video", "output"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict):
            nested = extract_task_id(inner)
            if nested:
                return nested
    return None


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

        if state in SUCCESS_STATES:
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


def task_log_path(args):
    """任务记录落在输出目录，保证中断后还能找回付费任务。"""
    if getattr(args, "log_file", None):
        return Path(args.log_file)
    base = Path(args.out).parent if args.out else Path("output")
    return base / TASK_LOG_NAME


def record_task(path, entry):
    """追加一条任务记录；写入失败不影响主流程。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as error:
        print(f"[video] ⚠ 任务记录写入失败：{error}", file=sys.stderr)
        return False


def read_task_entries(path):
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def latest_task(entries, only_pending=False):
    """取最近一条任务；only_pending 时跳过已完成/失败的。"""
    candidates = entries
    if only_pending:
        pending = [
            item for item in candidates
            if str(item.get("status", "")).lower() not in TERMINAL_STATES
        ]
        candidates = pending or candidates
    return candidates[-1] if candidates else None


def probe_media(path, timeout=20):
    """有 ffprobe 时读取真实时长/分辨率，没有就静默跳过。"""
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [
                executable, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration,codec_name",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        streams = json.loads(completed.stdout or "{}").get("streams") or []
    except json.JSONDecodeError:
        return None
    return streams[0] if streams else None


def describe_media(path):
    info = probe_media(path)
    if not info:
        return "（未检测到 ffprobe，跳过媒体校验）"
    parts = []
    if info.get("width") and info.get("height"):
        parts.append(f"{info['width']}x{info['height']}")
    duration = info.get("duration")
    if duration:
        try:
            parts.append(f"{float(duration):.1f}s")
        except ValueError:
            parts.append(f"{duration}s")
    if info.get("codec_name"):
        parts.append(str(info["codec_name"]))
    return "，".join(parts) or "媒体信息为空"


def default_output_path():
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("output") / f"video_{stamp}.mp4"


def download_with_retries(base_url, endpoint, task_id, payload, output, api_key, timeout):
    """下载失败重试 3 次；任务已完成，重试不产生额外费用。"""
    last_error = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            return download_completed_video(
                base_url, endpoint, task_id, payload, output, api_key, timeout
            )
        except Exception as error:  # noqa: BLE001 - 统一转为重试或抛出
            last_error = error
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                break
            delay = DOWNLOAD_DELAYS[min(attempt, len(DOWNLOAD_DELAYS) - 1)]
            print(f"[video] 下载失败（第 {attempt + 1} 次）：{error}，{delay} 秒后重试")
            time.sleep(delay)
    raise RuntimeError(f"下载重试 {DOWNLOAD_ATTEMPTS} 次仍失败：{last_error}")


def print_task_list(entries, log_path):
    if not entries:
        print(f"本地没有任务记录：{log_path}")
        return
    print(f"任务记录：{log_path}（共 {len(entries)} 条）")
    for item in entries:
        print(
            f"  {item.get('created_at', '?')}  {item.get('task_id', '?')}  "
            f"status={item.get('status', '?')}  model={item.get('model', '?')}  "
            f"seconds={item.get('seconds', '?')}  out={item.get('out') or '-'}"
        )


def create_parser():
    parser = argparse.ArgumentParser(
        prog="genvideo",
        description="异步视频生成 CLI：创建任务、轮询状态并下载 MP4。",
    )
    parser.add_argument("prompt", nargs="?", help="视频提示词；使用 --task-id 时可省略")
    parser.add_argument("--model", default=None,
                        help=f"视频模型；默认 sub2 协议用 {DEFAULT_MODEL}，"
                             f"openai 协议用 {DEFAULT_OPENAI_VIDEO_MODEL}")
    parser.add_argument("--protocol", choices=[PROTOCOL_SUB2, PROTOCOL_OPENAI],
                        default=PROTOCOL_SUB2,
                        help="sub2（默认，Sub2/即梦扩展字段）或 openai（严格 OpenAI Videos API）")
    parser.add_argument("--size", help="输出宽高，openai 协议用，如 1280x720")
    parser.add_argument("--duration", type=int,
                        help="时长（数字），发送 duration 字段替代 seconds；实测有效")
    parser.add_argument("--resolution",
                        help="分辨率，如 720p / 480p / 1280x720，发送 resolution 字段替代 aspect_ratio；实测有效")
    parser.add_argument("--seconds", default=None,
                        help="视频时长；sub2 协议如 5/10/15，openai 协议只支持 4/8/12")
    parser.add_argument("--aspect-ratio", default="16:9",
                        help="画面比例，例如 16:9、9:16、1:1")
    parser.add_argument("--image", action="append", default=[],
                        help="参考图片 URL 或本地路径（配合 --auto-upload），可重复")
    parser.add_argument("--video", action="append", default=[],
                        help="参考视频 URL 或本地路径（配合 --auto-upload），可重复")
    parser.add_argument("--audio", action="append", default=[],
                        help="参考音频 URL 或本地路径（配合 --auto-upload），可重复")
    parser.add_argument("--auto-upload", choices=['catbox', 'telegraph', 'smms', 'imgbb'],
                        help="自动上传本地文件到图床（catbox=默认推荐，无需配置）")
    parser.add_argument("--task-id", help="续查已有任务，不创建新任务")
    parser.add_argument("--resume-last", dest="resume_last", action="store_true",
                        help="自动续查本地任务记录里最近一个未完成任务")
    parser.add_argument("--list-models", dest="list_models", action="store_true",
                        help="查询账号可用的视频模型（免费，不创建任务）")
    parser.add_argument("--list-tasks", dest="list_tasks", action="store_true",
                        help="列出本地记录的视频任务（不联网）")
    parser.add_argument("--log-file", dest="log_file",
                        help=f"任务记录文件，默认输出目录下的 {TASK_LOG_NAME}")
    parser.add_argument("--no-wait", action="store_true",
                        help="创建后立即返回 task_id，不轮询或下载")
    parser.add_argument("--out", "-o", help="输出 MP4 路径")
    parser.add_argument("--base-url", help="已禁用；地址只能来自 IMAGE_API_BASE")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"视频任务路径，默认 {DEFAULT_ENDPOINT}")
    parser.add_argument("--api-key", help="已禁用；Key 只能来自 GENIMG_API_KEY")
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
    configure_stdio()
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.poll_interval < 1:
        parser.error("--poll-interval 必须大于 0")
    if args.wait_timeout < 1 or args.request_timeout < 1:
        parser.error("超时参数必须大于 0")
    if args.task_id and args.dry_run:
        parser.error("--task-id 不能与 --dry-run 一起使用")

    if args.list_models:
        base_env = os.environ.get("IMAGE_API_BASE")
        if not base_env:
            print(
                "未设置 IMAGE_API_BASE，查不到模型列表。先执行：\n"
                "  export IMAGE_API_BASE='https://你的中转站/v1'\n"
                "  export GENIMG_API_KEY='sk-xxx'"
            )
            return 1
        names, error = fetch_models(base_env, os.environ.get("GENIMG_API_KEY"))
        print_video_models(names, error)
        return 0 if names is not None else 1

    log_path = task_log_path(args)

    if args.list_tasks:
        print_task_list(read_task_entries(log_path), log_path)
        return 0

    if args.resume_last:
        if args.task_id:
            parser.error("--resume-last 与 --task-id 只能二选一")
        last = latest_task(read_task_entries(log_path), only_pending=True)
        if not last or not last.get("task_id"):
            parser.error(f"本地没有可续查的任务记录：{log_path}")
        args.task_id = last["task_id"]
        if not args.out and last.get("out"):
            args.out = last["out"]
        print(f"[video] 从本地记录续查：{args.task_id}")

    if args.seconds is None:
        args.seconds = "4" if getattr(args, "protocol", PROTOCOL_SUB2) == PROTOCOL_OPENAI else "5"
    if not args.task_id and not args.prompt:
        parser.error("创建任务时缺少视频提示词")

    if args.base_url or args.api_key:
        parser.error(
            "安全限制：API 地址和 Key 只能通过 IMAGE_API_BASE、GENIMG_API_KEY 提供"
        )
    base_url = os.environ.get("IMAGE_API_BASE")
    api_key = os.environ.get("GENIMG_API_KEY")
    if not base_url:
        parser.error(
            "缺少 IMAGE_API_BASE。先执行：export IMAGE_API_BASE='https://你的中转站/v1'"
        )
    if not base_url.rstrip("/").endswith("/v1"):
        parser.error("IMAGE_API_BASE 必须包含并以 /v1 结尾")
    if not api_key and not args.dry_run:
        parser.error("缺少 GENIMG_API_KEY")
    setup_proxy(args.no_proxy)
    headers = build_headers(api_key)

    task_id = args.task_id
    if not task_id:
        try:
            param_body = parse_param_assignments(args.param)
            model_name = args.model or (
                DEFAULT_OPENAI_VIDEO_MODEL if args.protocol == PROTOCOL_OPENAI
                else DEFAULT_MODEL
            )
            if args.protocol == PROTOCOL_OPENAI:
                body = build_openai_video_body(
                    model_name,
                    args.prompt,
                    seconds=args.seconds,
                    aspect_ratio=args.aspect_ratio,
                    size=args.size,
                    images=args.image,
                    videos=args.video,
                    audios=args.audio,
                    auto_upload=args.auto_upload,
                )
            else:
                body = build_video_body(
                    model_name,
                    args.prompt,
                    seconds=args.seconds,
                    aspect_ratio=args.aspect_ratio,
                    images=args.image,
                    videos=args.video,
                    audios=args.audio,
                    auto_upload=args.auto_upload,
                    duration=args.duration,
                    resolution=args.resolution,
                )
            body = deep_merge(body, param_body)
        except ValueError as error:
            parser.error(str(error))
        create_url = join_url(base_url, args.endpoint)
        print(
            f"[video] POST {create_url} "
            f"(protocol={args.protocol}, model={model_name}, "
            f"{'duration=' + str(args.duration) if args.duration is not None else 'seconds=' + str(args.seconds)}, "
            f"{'resolution=' + str(args.resolution) if args.resolution else 'aspect_ratio=' + str(args.aspect_ratio)})"
        )
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
            hint = explain_create_error(status, payload, raw)
            if hint:
                print(f"[video] → {hint}")
            return 1
        task_id = extract_task_id(payload)
        if not task_id:
            print("[video] ✗ 返回中没有 task_id/id")
            if args.debug:
                print(json.dumps(sanitize(payload), ensure_ascii=False, indent=2))
            return 1
        record_task(
            log_path,
            {
                "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "task_id": task_id,
                "status": "created",
                "model": args.model,
                "prompt": args.prompt,
                "seconds": args.seconds,
                "aspect_ratio": args.aspect_ratio,
                "out": args.out,
            },
        )
        print(f"[video] ✓ 任务已创建：{task_id}")
        print(f"[video] 已记录到 {log_path.resolve()}")
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
        path, size = download_with_retries(
            base_url,
            args.endpoint,
            task_id,
            payload,
            output,
            api_key,
            args.request_timeout,
        )
        media = describe_media(path)
        print(f"[video] ✓ 保存 {path} ({size // 1024} KB)")
        print(f"[video] 媒体信息：{media}")
        entry = {
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "task_id": task_id,
            "status": "completed",
            "out": str(path),
            "bytes": size,
            "media": media,
        }
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if usage:
            # 上游可能回传计费信息（如 cost_in_usd_ticks），原样记录便于对账
            entry["usage"] = usage
            print(f"[video] 上游用量：{json.dumps(usage, ensure_ascii=False)}")
        record_task(log_path, entry)
        return 0
    except KeyboardInterrupt:
        print(f"\n[video] 已停止等待，任务仍可续查：python3 genvideo.py --task-id {task_id}")
        return 130
    except Exception as error:
        print(f"[video] ✗ {error}")
        print(f"任务 ID：{task_id}")
        print(f"续查：python3 genvideo.py --task-id {task_id}（记录在 {log_path}）")
        record_task(
            log_path,
            {
                "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "task_id": task_id,
                "status": "pending",
                "error": str(error),
            },
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
