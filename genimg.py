#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genimg — 通用生图 CLI，供 Claude Code / Codex / OpenCode 等 agent 直接调用。

特点：
  * 零第三方依赖（仅 Python3 标准库），无需 pip install。
  * 多中转站/多分组：一个 provider 一条配置，可同时对多个 provider 生图。
  * 格式无关：请求支持 chat | images | gemini 三种模式；响应用"万能解析器"
    自动从任意 JSON 结构里提取图片（url / base64 / markdown图链 / inlineData）。
  * --debug 打印真实返回结构，方便确认你中转站到底是什么格式。

用法示例：
  python genimg.py "一只戴墨镜的柴犬" --provider banana
  python genimg.py "赛博朋克城市夜景" --provider banana,image2      # 同时两个分组
  python genimg.py "把这张图变成水彩" --provider banana --image in.png
  python genimg.py "test" --provider banana --debug                # 看原始返回
"""

import argparse
import base64
import datetime as _dt
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_NAMES = ["providers.json", "providers.local.json", "providers.example.json"]

DEFAULT_ENDPOINT = {
    "chat": "/chat/completions",
    "images": "/images/generations",
    "gemini": "/v1beta/models/{model}:generateContent",
}

# ----------------------------------------------------------------------------- config

def load_config(explicit=None):
    """按优先级找配置文件：--config 指定 > 当前目录 > 脚本目录。"""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    for name in CONFIG_NAMES:
        candidates.append(Path.cwd() / name)
        candidates.append(HERE / name)
    for p in candidates:
        if p and p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.pop("_comment", None)
            return data, p
    return {}, None


def resolve_provider(name, config, args):
    """合并配置：CLI 参数 > 环境变量 > 配置文件 > 内置默认。"""
    cfg = dict(config.get(name, {}))
    base_url = args.base_url or os.environ.get("IMAGE_API_BASE") or cfg.get("base_url")
    mode = args.mode or cfg.get("mode") or "chat"
    model = args.model or cfg.get("model") or name
    endpoint = args.endpoint or cfg.get("endpoint") or DEFAULT_ENDPOINT.get(mode, "/chat/completions")

    key_env = cfg.get("api_key_env", "IMAGE_API_KEY")
    api_key = (
        args.api_key
        or os.environ.get(key_env)
        or os.environ.get("IMAGE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not base_url:
        raise ValueError(
            f"provider '{name}' 缺少 base_url。请在 providers.json 里配置，或用 --base-url 指定。"
        )
    return {
        "name": name,
        "base_url": base_url,
        "mode": mode,
        "model": model,
        "endpoint": endpoint,
        "api_key": api_key,
    }

# ----------------------------------------------------------------------------- http

def join_url(base, endpoint, model):
    base = base.rstrip("/")
    endpoint = (endpoint or "").format(model=model)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base + endpoint


def build_headers(mode, api_key):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
        if mode == "gemini":
            h["x-goog-api-key"] = api_key
    return h


def setup_proxy(no_proxy):
    """默认继承系统代理（HTTP_PROXY 等）；--no-proxy 时强制直连，
    适合本地中转站或代理会拦截 502 的情况。"""
    if no_proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        urllib.request.install_opener(opener)


def http_post_json(url, headers, body, timeout):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_get_bytes(url, timeout):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

# ----------------------------------------------------------------------------- request builders

def file_to_data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b}", mime, b


def build_body(mode, model, prompt, size, n, images, quality=None, response_format=None, aspect_ratio=None):
    if mode == "chat":
        if images:
            content = [{"type": "text", "text": prompt}]
            for img in images:
                uri, _, _ = file_to_data_uri(img)
                content.append({"type": "image_url", "image_url": {"url": uri}})
        else:
            content = prompt
        return {"model": model, "messages": [{"role": "user", "content": content}], "stream": False}

    if mode == "images":
        body = {"model": model, "prompt": prompt, "n": n}
        if size:
            body["size"] = size
        if quality:
            body["quality"] = quality
        if response_format:
            body["response_format"] = response_format

        # 构建 extra_fields（中转站扩展参数）
        extra = {}
        if aspect_ratio:
            extra["aspect_ratio"] = aspect_ratio
            if "google" not in extra:
                extra["google"] = {"image_config": {}}
            extra["google"]["image_config"]["aspect_ratio"] = aspect_ratio

        if quality in ["2K", "4K"]:
            extra["image_size"] = quality
            if "google" not in extra:
                extra["google"] = {"image_config": {}}
            extra["google"]["image_config"]["image_size"] = quality

        if extra:
            body["extra_fields"] = extra

        return body

    if mode == "gemini":
        parts = [{"text": prompt}]
        for img in images or []:
            _, mime, b = file_to_data_uri(img)
            parts.append({"inline_data": {"mime_type": mime, "data": b}})
        return {"contents": [{"parts": parts}]}

    raise ValueError(f"未知 mode: {mode}（可选 chat | images | gemini）")

# ----------------------------------------------------------------------------- 万能响应解析

DATA_URI_RE = re.compile(r"data:image/[\w.+-]+;base64,([A-Za-z0-9+/=\s]+)")
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
BARE_URL_RE = re.compile(r"https?://[^\s\"')]+\.(?:png|jpe?g|webp|gif)", re.I)


def extract_images(obj):
    """递归扫描任意 JSON，抽出所有图片。返回 [('b64'|'url', payload), ...] 去重。"""
    found = []
    _walk(obj, found, parent_key=None)
    seen, out = set(), []
    for kind, val in found:
        k = (kind, val[:64])
        if k not in seen:
            seen.add(k)
            out.append((kind, val))
    return out


def _walk(node, found, parent_key):
    if isinstance(node, dict):
        for k, v in node.items():
            kl = k.lower()
            if kl in ("b64_json", "b64", "image_base64", "imagebytes") and isinstance(v, str):
                found.append(("b64", v))
            elif kl in ("inline_data", "inlinedata") and isinstance(v, dict) and isinstance(v.get("data"), str):
                found.append(("b64", v["data"]))
            elif kl == "data" and isinstance(v, str) and parent_key in ("inline_data", "inlinedata"):
                found.append(("b64", v))
            elif kl == "url" and isinstance(v, str):
                if v.startswith("data:image"):
                    m = DATA_URI_RE.search(v)
                    if m:
                        found.append(("b64", m.group(1)))
                else:
                    found.append(("url", v))
            else:
                _walk(v, found, kl)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found, parent_key)
    elif isinstance(node, str):
        for m in DATA_URI_RE.findall(node):
            found.append(("b64", m))
        for m in MD_IMG_RE.findall(node):
            found.append(("url", m))
        for m in BARE_URL_RE.findall(node):
            found.append(("url", m))

# ----------------------------------------------------------------------------- 保存

def clean_b64(s):
    s = re.sub(r"\s+", "", s)
    if s.lower().startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return s


def sniff_ext(raw):
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".png"


def save_image(kind, payload, out_base, timeout):
    if kind == "b64":
        raw = base64.b64decode(clean_b64(payload))
    else:
        raw = http_get_bytes(payload, timeout)
    ext = sniff_ext(raw)
    out_path = out_base.with_suffix(ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(raw)
    return out_path, len(raw)

# ----------------------------------------------------------------------------- debug

def sanitize(obj, limit=180):
    if isinstance(obj, dict):
        return {k: sanitize(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return obj[:limit] + f"...<+{len(obj) - limit} chars>"
    return obj

# ----------------------------------------------------------------------------- 单 provider 执行

def run_one(prov, prompt, args, single_target=True):
    label = prov["name"]
    url = join_url(prov["base_url"], prov["endpoint"], prov["model"])
    headers = build_headers(prov["mode"], prov["api_key"])
    body = build_body(
        prov["mode"], prov["model"], prompt, args.size, args.n, args.image,
        quality=getattr(args, 'quality', None),
        response_format=getattr(args, 'response_format', None),
        aspect_ratio=getattr(args, 'aspect_ratio', None)
    )

    print(f"[{label}] POST {url}  (mode={prov['mode']}, model={prov['model']})")
    if not prov["api_key"]:
        print(f"[{label}] ⚠ 未找到 API key（设置环境变量 IMAGE_API_KEY 或用 --api-key）")

    status, raw = http_post_json(url, headers, body, args.timeout)

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = None

    if args.debug:
        print(f"[{label}] HTTP {status}")
        if parsed is not None:
            print(json.dumps(sanitize(parsed), ensure_ascii=False, indent=2))
        else:
            print(raw.decode("utf-8", "replace")[:2000])

    if status >= 300:
        snippet = raw.decode("utf-8", "replace")[:500]
        print(f"[{label}] ✗ 请求失败 HTTP {status}: {snippet}")
        return []

    if parsed is None:
        print(f"[{label}] ✗ 返回不是 JSON，无法解析。用 --debug 查看原始内容。")
        return []

    images = extract_images(parsed)
    if not images:
        print(f"[{label}] ✗ 没在返回里找到图片。用 --debug 看结构，可能要换 --mode。")
        return []

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    saved = []
    for i, (kind, payload) in enumerate(images):
        if args.out and len(images) == 1 and single_target:
            out_base = Path(args.out)
        else:
            suffix = f"_{i}" if len(images) > 1 else ""
            out_base = Path(args.outdir) / f"{label}_{stamp}{suffix}"
        try:
            path, nbytes = save_image(kind, payload, out_base, args.timeout)
            print(f"[{label}] ✓ 保存 {path}  ({nbytes // 1024} KB, 来源 {kind})")
            saved.append(str(path))
        except Exception as e:
            print(f"[{label}] ✗ 保存失败: {e}")
    return saved

# ----------------------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="genimg",
        description="通用生图 CLI：多中转站/多分组，格式无关。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", nargs="?", help="生图提示词")
    p.add_argument("--provider", "-p", default="banana",
                   help="provider 名，逗号分隔可同时多个；'all' 表示配置里全部。默认 banana")
    p.add_argument("--image", "-i", action="append", default=[],
                   help="输入图（图生图/编辑），可多次；仅 chat/gemini 模式有效")
    p.add_argument("--out", "-o", help="单张输出时的文件名")
    p.add_argument("--outdir", default="output", help="输出目录，默认 ./output")
    p.add_argument("--model", "-m", help="覆盖模型名")
    p.add_argument("--base-url", dest="base_url", help="覆盖中转站 base_url")
    p.add_argument("--mode", choices=["chat", "images", "gemini"], help="覆盖请求模式")
    p.add_argument("--endpoint", help="覆盖请求路径，可含 {model}")
    p.add_argument("--api-key", dest="api_key", help="覆盖 API key")
    p.add_argument("--size", help="尺寸，如 1024x1024（images 模式）或 9:16（Gemini 比例）")
    p.add_argument("--quality", help="质量，如 auto、low、high、2K、4K")
    p.add_argument("--response-format", dest="response_format", help="返回格式：url 或 b64_json")
    p.add_argument("--aspect-ratio", dest="aspect_ratio", help="宽高比，如 9:16、16:9")
    p.add_argument("--n", type=int, default=1, help="生成数量（images 模式）")
    p.add_argument("--timeout", type=int, default=180, help="超时秒数，默认 180")
    p.add_argument("--config", help="指定 providers 配置文件")
    p.add_argument("--no-proxy", dest="no_proxy", action="store_true",
                   help="强制直连，忽略系统代理（本地中转站/代理拦截时用）")
    p.add_argument("--debug", action="store_true", help="打印原始返回结构")
    p.add_argument("--list", action="store_true", help="列出已配置的 provider")
    args = p.parse_args(argv)

    setup_proxy(args.no_proxy)
    config, cfg_path = load_config(args.config)

    if args.list:
        if not config:
            print("没找到配置文件。复制 providers.example.json 为 providers.json 并填写。")
        else:
            print(f"配置文件: {cfg_path}")
            for name, c in config.items():
                print(f"  - {name}: mode={c.get('mode')}, model={c.get('model')}, base={c.get('base_url')}")
        return 0

    if not args.prompt:
        p.error("缺少提示词。示例: python genimg.py \"一只柴犬\" --provider banana")

    # 解析目标 provider 列表
    if args.provider.lower() == "all":
        names = list(config.keys()) or ["banana"]
    else:
        names = [x.strip() for x in args.provider.split(",") if x.strip()]

    all_saved = []
    for name in names:
        try:
            prov = resolve_provider(name, config, args)
        except ValueError as e:
            print(f"[{name}] ✗ {e}")
            continue
        try:
            all_saved.extend(run_one(prov, args.prompt, args, single_target=len(names) == 1))
        except urllib.error.URLError as e:
            print(f"[{name}] ✗ 网络错误: {e}")
        except Exception as e:
            print(f"[{name}] ✗ 出错: {e}")

    print("-" * 40)
    if all_saved:
        print(f"完成，共保存 {len(all_saved)} 张:")
        for s in all_saved:
            print(f"  {s}")
        return 0
    print("未生成任何图片。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
