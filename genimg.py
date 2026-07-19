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
  python genimg.py "产品海报" --provider image2 --size 1536x1024
  python genimg.py "test" --provider banana --debug                # 看原始返回
"""

import argparse
import base64
import datetime as _dt
import json
import mimetypes
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_NAMES = ["providers.json", "providers.local.json", "providers.example.json"]
DEFAULT_EDIT_ENDPOINT = "/images/edits"

DEFAULT_ENDPOINT = {
    "chat": "/chat/completions",
    "images": "/images/generations",
    "gemini": "/v1beta/models/{model}:generateContent",
}

REQUEST_OPTION_NAMES = (
    "size",
    "quality",
    "response_format",
    "aspect_ratio",
    "n",
    "output_format",
    "output_compression",
    "background",
    "moderation",
    "style",
    "user",
)

GEMINI_IMAGE_SIZES = {"0.5K", "512", "1K", "2K", "4K"}

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
    edit_mode = args.edit_mode or cfg.get("edit_mode") or mode
    edit_default_endpoint = (
        DEFAULT_EDIT_ENDPOINT if edit_mode == "images"
        else DEFAULT_ENDPOINT.get(edit_mode, "/chat/completions")
    )
    edit_endpoint = args.edit_endpoint or cfg.get("edit_endpoint") or edit_default_endpoint

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
    defaults = cfg.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError(f"provider '{name}' 的 defaults 必须是 JSON object。")

    request_options = {}
    for option_name in REQUEST_OPTION_NAMES:
        cli_value = getattr(args, option_name, None)
        if cli_value is not None:
            request_options[option_name] = cli_value
        elif option_name in defaults:
            request_options[option_name] = defaults[option_name]
        elif option_name in cfg:
            # 兼容旧配置把请求参数直接放在 provider 下的写法。
            request_options[option_name] = cfg[option_name]

    request_options.setdefault("n", 1)
    if not isinstance(request_options["n"], int) or not 1 <= request_options["n"] <= 10:
        raise ValueError(f"provider '{name}' 的 n 必须是 1 到 10 的整数。")
    compression = request_options.get("output_compression")
    if compression is not None and (
        not isinstance(compression, int) or not 0 <= compression <= 100
    ):
        raise ValueError(f"provider '{name}' 的 output_compression 必须是 0 到 100 的整数。")
    provider_extra_body = cfg.get("extra_body", {})
    if provider_extra_body is None:
        provider_extra_body = {}
    if not isinstance(provider_extra_body, dict):
        raise ValueError(f"provider '{name}' 的 extra_body 必须是 JSON object。")

    return {
        "name": name,
        "base_url": base_url,
        "mode": mode,
        "edit_mode": edit_mode,
        "model": model,
        "endpoint": endpoint,
        "edit_endpoint": edit_endpoint,
        "api_key": api_key,
        "request_options": request_options,
        "extra_body": provider_extra_body,
    }

# ----------------------------------------------------------------------------- http

def join_url(base, endpoint, model):
    base = base.rstrip("/")
    endpoint = (endpoint or "").format(model=model)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base + endpoint


def build_headers(mode, api_key, content_type="application/json"):
    is_multipart = content_type.startswith("multipart/form-data")
    h = {
        "Content-Type": content_type,
        "Accept": "*/*" if is_multipart else "application/json",
        # 一些中转站/WAF 会截断带浏览器 UA 的大 multipart 请求；curl UA
        # 与 OpenAI 官方 curl 示例的传输行为一致，同时保留 JSON 请求原有 UA。
        "User-Agent": (
            "curl/8.7.1" if is_multipart else
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
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


def http_post_bytes(url, headers, data, timeout):
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


def deep_merge(base, override):
    """递归合并 JSON object；override 优先。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_param_assignments(assignments):
    """把重复的 --param a.b=value 转成可深度合并的 JSON object。"""
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


def _relay_extra_fields(options):
    """保留已有中转站的 extra_fields 扩展格式。"""
    extra = {}
    aspect_ratio = options.get("aspect_ratio")
    quality = options.get("quality")
    if isinstance(quality, str) and quality.upper() in GEMINI_IMAGE_SIZES:
        quality = quality.upper()
    if aspect_ratio:
        extra["aspect_ratio"] = aspect_ratio
        extra.setdefault("google", {}).setdefault("image_config", {})["aspect_ratio"] = aspect_ratio
    if quality in GEMINI_IMAGE_SIZES:
        extra["image_size"] = quality
        extra.setdefault("google", {}).setdefault("image_config", {})["image_size"] = quality
    return extra


def build_edit_fields(model, prompt, options=None, extra_body=None):
    """构建 OpenAI-compatible /images/edits 的 multipart 文本字段。"""
    options = dict(options or {})
    extra_body = dict(extra_body or {})
    fields = {"model": model, "prompt": prompt, "n": options.get("n", 1)}
    if "dall-e-3" in model.lower() and fields["n"] != 1:
        raise ValueError("DALL-E 3 仅支持 n=1。")
    if options.get("output_compression") is not None and options.get("output_format") not in (
        "jpeg", "webp",
    ):
        raise ValueError("output_compression 只适用于 output_format=jpeg 或 webp。")
    for name in (
        "size", "quality", "response_format", "output_format", "output_compression",
        "background", "moderation", "style", "user",
    ):
        if options.get(name) is not None:
            fields[name] = options[name]

    extra = _relay_extra_fields(options)
    if extra:
        fields["extra_fields"] = extra
    return deep_merge(extra_body, fields)


def _multipart_text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_multipart(fields, images, mask=None):
    """把文本字段和本地图片编码为 multipart/form-data。"""
    boundary = "----genimg-" + secrets.token_hex(16)
    chunks = []

    def add_line(value=b""):
        chunks.append(value if isinstance(value, bytes) else value.encode("utf-8"))
        chunks.append(b"\r\n")

    for name, value in fields.items():
        if value is None:
            continue
        add_line(f"--{boundary}")
        add_line(f'Content-Disposition: form-data; name="{name}"')
        add_line()
        add_line(_multipart_text(value))

    file_items = [("image[]", path) for path in images]
    if mask:
        file_items.append(("mask", mask))
    for field_name, raw_path in file_items:
        path = Path(raw_path)
        if not path.is_file():
            raise ValueError(f"输入图片不存在或不是文件：{path}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = path.name.replace('"', "_")
        add_line(f"--{boundary}")
        add_line(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"'
        )
        add_line(f"Content-Type: {mime}")
        add_line()
        add_line(path.read_bytes())

    add_line(f"--{boundary}--")
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def build_body(mode, model, prompt, images, options=None, extra_body=None):
    options = dict(options or {})
    extra_body = dict(extra_body or {})
    ignored = []

    if mode == "chat":
        if images:
            content = [{"type": "text", "text": prompt}]
            for img in images:
                uri, _, _ = file_to_data_uri(img)
                content.append({"type": "image_url", "image_url": {"url": uri}})
        else:
            content = prompt
        body = {"model": model, "messages": [{"role": "user", "content": content}], "stream": False}
        if options.get("n") not in (None, 1):
            body["n"] = options["n"]
        for name in (
            "size", "quality", "response_format", "aspect_ratio", "output_format",
            "output_compression", "background", "moderation", "style", "user",
        ):
            if options.get(name) is not None:
                ignored.append(name)
        return deep_merge(extra_body, body), ignored

    if mode == "images":
        if images:
            raise ValueError(
                "images mode 的图片编辑应由 /images/edits multipart 流程处理，"
                "不能调用 build_body 构建 JSON 请求。"
            )
        body = {"model": model, "prompt": prompt, "n": options.get("n", 1)}
        if "dall-e-3" in model.lower() and body["n"] != 1:
            raise ValueError("DALL-E 3 仅支持 n=1。")
        if options.get("output_compression") is not None and options.get("output_format") not in (
            "jpeg", "webp",
        ):
            raise ValueError("output_compression 只适用于 output_format=jpeg 或 webp。")
        for name in (
            "size", "quality", "response_format", "output_format", "output_compression",
            "background", "moderation", "style", "user",
        ):
            if options.get(name) is not None:
                body[name] = options[name]

        # aspect_ratio 与 K 级分辨率不是 OpenAI 标准字段，但保留项目原有的
        # extra_fields 中转站扩展协议。
        extra = _relay_extra_fields(options)
        if extra:
            body["extra_fields"] = extra
        return deep_merge(extra_body, body), ignored

    if mode == "gemini":
        parts = [{"text": prompt}]
        for img in images or []:
            _, mime, b = file_to_data_uri(img)
            parts.append({"inline_data": {"mime_type": mime, "data": b}})
        body = {"contents": [{"parts": parts}]}
        generation_config = {"responseModalities": ["IMAGE"]}
        image_config = {}

        aspect_ratio = options.get("aspect_ratio")
        size = options.get("size")
        quality = options.get("quality")
        if aspect_ratio:
            image_config["aspectRatio"] = aspect_ratio
        elif isinstance(size, str) and ":" in size:
            image_config["aspectRatio"] = size
        elif size is not None and str(size).upper() not in GEMINI_IMAGE_SIZES:
            ignored.append("size")

        image_size = None
        if isinstance(quality, str) and quality.upper() in GEMINI_IMAGE_SIZES:
            image_size = quality.upper()
        elif isinstance(size, str) and size.upper() in GEMINI_IMAGE_SIZES:
            image_size = size.upper()
        elif quality is not None:
            ignored.append("quality")
        if image_size:
            image_config["imageSize"] = image_size

        if image_config:
            generation_config["responseFormat"] = {"image": image_config}
        body["generationConfig"] = generation_config

        for name in (
            "response_format", "output_format", "output_compression", "background",
            "moderation", "style", "user",
        ):
            if options.get(name) is not None:
                ignored.append(name)
        if options.get("n") not in (None, 1):
            ignored.append("n")
        return deep_merge(extra_body, body), ignored

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
    is_edit = bool(args.image)
    request_mode = prov["edit_mode"] if is_edit else prov["mode"]
    request_endpoint = prov["edit_endpoint"] if is_edit else prov["endpoint"]
    is_multipart_edit = is_edit and request_mode == "images"
    if args.mask and not is_multipart_edit:
        raise ValueError("--mask 只能与 edit_mode=images 的 --image 编辑请求一起使用。")

    if is_multipart_edit:
        url = join_url(prov["base_url"], request_endpoint, prov["model"])
        fields = build_edit_fields(
            prov["model"], prompt,
            options=prov["request_options"],
            extra_body=prov["extra_body"],
        )
        # --param 在编辑请求中覆盖 multipart 文本字段。
        fields = deep_merge(fields, args.param_body)
        request_data, content_type = build_multipart(fields, args.image, args.mask)
        headers = build_headers(request_mode, prov["api_key"], content_type)
        ignored = []
        debug_body = {
            "fields": fields,
            "image[]": [str(Path(path)) for path in args.image],
            "mask": str(Path(args.mask)) if args.mask else None,
        }
    else:
        url = join_url(prov["base_url"], request_endpoint, prov["model"])
        headers = build_headers(request_mode, prov["api_key"])
        body, ignored = build_body(
            request_mode, prov["model"], prompt, args.image,
            options=prov["request_options"],
            extra_body=prov["extra_body"],
        )
        # --param 是显式的低层请求体覆盖，优先级高于所有结构化参数。
        body = deep_merge(body, args.param_body)
        debug_body = body

    operation = "edit" if is_edit else "generate"
    print(
        f"[{label}] POST {url}  "
        f"(mode={request_mode}, operation={operation}, model={prov['model']})"
    )
    if ignored:
        print(
            f"[{label}] ⚠ mode={request_mode} 没有通用映射，已忽略: {', '.join(sorted(set(ignored)))}；"
            "中转站自定义字段请用 --param key=value"
        )
    if not prov["api_key"]:
        print(f"[{label}] ⚠ 未找到 API key（设置环境变量 IMAGE_API_KEY 或用 --api-key）")

    if args.debug or args.dry_run:
        print(f"[{label}] 请求参数:")
        print(json.dumps(sanitize(debug_body), ensure_ascii=False, indent=2))
    if args.dry_run:
        print(f"[{label}] ✓ dry-run，未发送网络请求")
        return []

    if is_multipart_edit:
        status, raw = http_post_bytes(url, headers, request_data, args.timeout)
    else:
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
                   help="输入图（图生图/编辑），可多次；自动使用 provider 的编辑路由")
    p.add_argument("--mask", help="编辑遮罩图；仅与 images mode 的 --image 一起使用")
    p.add_argument("--out", "-o", help="单张输出时的文件名")
    p.add_argument("--outdir", default="output", help="输出目录，默认 ./output")
    p.add_argument("--model", "-m", help="覆盖模型名")
    p.add_argument("--base-url", dest="base_url", help="覆盖中转站 base_url")
    p.add_argument("--mode", choices=["chat", "images", "gemini"], help="覆盖请求模式")
    p.add_argument("--edit-mode", dest="edit_mode", choices=["chat", "images", "gemini"],
                   help="覆盖传入 --image 时使用的请求模式")
    p.add_argument("--endpoint", help="覆盖请求路径，可含 {model}")
    p.add_argument("--edit-endpoint", dest="edit_endpoint",
                   help="覆盖图片编辑请求路径")
    p.add_argument("--api-key", dest="api_key", help="覆盖 API key")
    p.add_argument("--size", help="尺寸，如 1024x1024（images）或 1K/2K/4K（gemini）")
    p.add_argument("--quality", help="质量，如 auto、low、high、2K、4K")
    p.add_argument("--response-format", dest="response_format", choices=["url", "b64_json"],
                   help="返回格式（DALL-E 2/3）：url 或 b64_json")
    p.add_argument("--aspect-ratio", dest="aspect_ratio", help="宽高比，如 9:16、16:9")
    p.add_argument("--output-format", dest="output_format", choices=["png", "jpeg", "webp"],
                   help="输出编码（GPT Image）：png、jpeg 或 webp")
    p.add_argument("--output-compression", dest="output_compression", type=int,
                   help="JPEG/WEBP 压缩质量 0-100（GPT Image）")
    p.add_argument("--background", choices=["auto", "transparent", "opaque"],
                   help="背景（GPT Image）：auto、transparent 或 opaque")
    p.add_argument("--moderation", choices=["auto", "low"], help="内容审核级别（GPT Image）")
    p.add_argument("--style", choices=["vivid", "natural"], help="风格（仅 DALL-E 3）")
    p.add_argument("--user", help="终端用户标识（OpenAI Images）")
    p.add_argument("--n", type=int, default=None, help="生成数量（images；默认 1）")
    p.add_argument("--timeout", type=int, default=180, help="超时秒数，默认 180")
    p.add_argument("--config", help="指定 providers 配置文件")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="追加/覆盖自定义 JSON 请求字段，可重复，支持 a.b=value")
    p.add_argument("--no-proxy", dest="no_proxy", action="store_true",
                   help="强制直连，忽略系统代理（本地中转站/代理拦截时用）")
    p.add_argument("--debug", action="store_true", help="打印原始返回结构")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="打印最终 URL/请求体但不发送请求")
    p.add_argument("--list", action="store_true", help="列出已配置的 provider")
    args = p.parse_args(argv)

    if args.n is not None and not 1 <= args.n <= 10:
        p.error("--n 必须在 1 到 10 之间")
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        p.error("--output-compression 必须在 0 到 100 之间")
    try:
        args.param_body = parse_param_assignments(args.param)
    except ValueError as e:
        p.error(str(e))

    setup_proxy(args.no_proxy)
    config, cfg_path = load_config(args.config)

    if args.list:
        if not config:
            print("没找到配置文件。复制 providers.example.json 为 providers.json 并填写。")
        else:
            print(f"配置文件: {cfg_path}")
            for name, c in config.items():
                defaults = json.dumps(c.get("defaults", {}), ensure_ascii=False)
                print(
                    f"  - {name}: mode={c.get('mode')}, model={c.get('model')}, "
                    f"base={c.get('base_url')}, defaults={defaults}"
                )
        return 0

    if not args.prompt:
        p.error("缺少提示词。示例: python genimg.py \"一只柴犬\" --provider banana")

    # 解析目标 provider 列表
    if args.provider.lower() == "all":
        names = list(config.keys()) or ["banana"]
    else:
        names = [x.strip() for x in args.provider.split(",") if x.strip()]

    all_saved = []
    completed_runs = 0
    for name in names:
        try:
            prov = resolve_provider(name, config, args)
        except ValueError as e:
            print(f"[{name}] ✗ {e}")
            continue
        try:
            all_saved.extend(run_one(prov, args.prompt, args, single_target=len(names) == 1))
            completed_runs += 1
        except urllib.error.URLError as e:
            print(f"[{name}] ✗ 网络错误: {e}")
        except Exception as e:
            print(f"[{name}] ✗ 出错: {e}")

    print("-" * 40)
    if args.dry_run:
        if completed_runs:
            print("dry-run 完成，未生成图片。")
            return 0
        print("dry-run 失败，没有构建出任何请求。")
        return 1
    if all_saved:
        print(f"完成，共保存 {len(all_saved)} 张:")
        for s in all_saved:
            print(f"  {s}")
        return 0
    print("未生成任何图片。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
