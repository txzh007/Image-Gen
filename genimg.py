#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genimg — 跨平台 Sub2API 图片 CLI，供 Claude Code / Codex / OpenCode 等 agent 调用。

特点：
  * 零第三方依赖（仅 Python 3 标准库），兼容 Windows、macOS、Linux。
  * 只通过 Sub2 OpenAI Images 兼容接口生成和编辑图片。
  * 本地文件、URL、Data URL、Base64 统一转为内存 multipart 文件。
  * 自动解析 data[].url 与 data[].b64_json，并对调试输出去敏。

用法示例：
  python genimg.py "一只戴墨镜的柴犬" --provider banana
  python genimg.py "赛博朋克城市夜景" --provider banana,image2      # 同时两个分组
  python genimg.py "产品海报" --provider image2 --size 1536x1024
  python genimg.py "test" --provider banana --debug                # 看原始返回
"""

import argparse
import base64
import binascii
import datetime as _dt
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_NAMES = ["providers.json", "providers.local.json", "providers.example.json"]
GENERATION_ENDPOINT = "/images/generations"
EDIT_ENDPOINT = "/images/edits"

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
DEFAULT_SUB2_IMAGE_MODEL = "gpt-image-gemini-flash"
ALLOWED_SUB2_IMAGE_MODELS = {
    "gpt-image-gemini-flash",
    "gpt-image-gemini-pro",
    "gpt-image-2",
}
SUB2_MODEL_ALIASES = {
    "gemini-3.1-flash-image": "gpt-image-gemini-flash",
    "gemini-3-pro-image": "gpt-image-gemini-pro",
}
RETRYABLE_HTTP_STATUSES = {429, 503}
RETRY_DELAYS = (2, 4, 8)
MAX_IMAGE_INPUT_BYTES = 20 * 1024 * 1024
RASTER_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# ----------------------------------------------------------------------------- config

def normalize_sub2_model(model):
    """把已知 Gemini 上游名固定映射为 Sub2API image 别名。"""
    return SUB2_MODEL_ALIASES.get(model, model)


def validate_sub2_model(model):
    """确保发往 Sub2 Images API 的模型是允许的兼容别名。"""
    normalized = normalize_sub2_model(model)
    if normalized not in ALLOWED_SUB2_IMAGE_MODELS:
        allowed = "、".join(sorted(ALLOWED_SUB2_IMAGE_MODELS))
        raise ValueError(f"不支持的图片模型 '{model}'；只能使用：{allowed}。")
    return normalized

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
    """合并 provider 配置；地址和 Key 只从旧版环境变量读取。"""
    cfg = dict(config.get(name, {}))
    image_api_base = os.environ.get("IMAGE_API_BASE")
    api_key = os.environ.get("GENIMG_API_KEY")
    if not image_api_base:
        raise ValueError("缺少 IMAGE_API_BASE（必须包含并以 /v1 结尾）。")
    if not image_api_base.rstrip("/").endswith("/v1"):
        raise ValueError("IMAGE_API_BASE 必须包含并以 /v1 结尾。")
    if not api_key and not getattr(args, "dry_run", False):
        raise ValueError("缺少 GENIMG_API_KEY。")

    model = args.model or cfg.get("model") or (
        DEFAULT_SUB2_IMAGE_MODEL if name == "banana" else name
    )
    model = validate_sub2_model(model)
    if (
        name == "banana"
        and model == "gpt-image-gemini-pro"
        and not args.model
    ):
        raise ValueError(
            "banana 的 Pro 模型必须由用户明确指定："
            "--model gpt-image-gemini-pro。"
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
        "base_url": image_api_base,
        "model": model,
        "api_key": api_key,
        "request_options": request_options,
        "extra_body": provider_extra_body,
    }

# ----------------------------------------------------------------------------- http

def join_url(base, endpoint):
    base = base.rstrip("/")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base + endpoint


def build_headers(api_key, content_type="application/json"):
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


def post_with_retries(post_once, label="request", delays=RETRY_DELAYS, sleep_fn=time.sleep):
    """仅对 429/503 退避重试；不切换 provider 或模型。"""
    status, raw = post_once()
    for delay in delays:
        if status not in RETRYABLE_HTTP_STATUSES:
            break
        print(f"[{label}] HTTP {status}，{delay} 秒后重试（保持原模型）")
        sleep_fn(delay)
        status, raw = post_once()
    return status, raw


def http_get_bytes(url, timeout):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sniff_input_mime(raw):
    """根据文件签名识别常见栅格图片 MIME。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:2] == b"BM":
        return "image/bmp"
    if raw[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return None


def _validated_image_upload(raw, filename, declared_mime=None, source="input"):
    if not raw:
        raise ValueError(f"输入图片为空：{source}")
    if len(raw) > MAX_IMAGE_INPUT_BYTES:
        raise ValueError(f"输入图片超过 20MB：{source}")
    detected_mime = sniff_input_mime(raw)
    if not detected_mime:
        raise ValueError(f"输入内容不是受支持的栅格图片：{source}")
    if declared_mime and not declared_mime.lower().startswith("image/"):
        raise ValueError(f"图片 MIME 类型无效：{declared_mime}")
    safe_name = (filename or "input").replace('"', "_")
    ext = Path(safe_name).suffix.lower()
    if not ext or RASTER_MIME_BY_EXT.get(ext) != detected_mime:
        ext_by_mime = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
        }
        safe_name = Path(safe_name).stem + ext_by_mime[detected_mime]
    return {
        "filename": safe_name,
        "mime": detected_mime,
        "data": raw,
        "source": source,
    }


def load_image_input(value, timeout=60):
    """把本地文件、Base64、Data URL 或公网 URL 转成内存文件。"""
    value = str(value)
    if value.startswith("data:"):
        try:
            header, encoded = value.split(",", 1)
            mime, encoding = header[5:].split(";", 1)
            if encoding.lower() != "base64":
                raise ValueError("Data URL 必须使用 base64 编码。")
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"无效的图片 Data URL：{exc}") from exc
        return _validated_image_upload(raw, "input", mime, source="data-url")

    if re.match(r"^https?://", value, re.I):
        req = urllib.request.Request(
            value,
            headers={"Accept": "image/*", "User-Agent": "Image-Gen/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                declared_mime = resp.headers.get_content_type()
                raw = resp.read(MAX_IMAGE_INPUT_BYTES + 1)
                final_url = resp.geturl()
        except urllib.error.URLError as exc:
            raise ValueError(f"下载图片 URL 失败：{exc}") from exc
        filename = Path(urllib.parse.urlparse(final_url).path).name or "input"
        return _validated_image_upload(raw, filename, declared_mime, source="url")

    try:
        path = Path(value)
        if path.is_file():
            raw = path.read_bytes()
            declared_mime = mimetypes.guess_type(path.name)[0]
            return _validated_image_upload(raw, path.name, declared_mime, source="local-file")
    except OSError:
        # 很长的 Base64 字符串不是文件路径；继续按 Base64 解码。
        pass

    try:
        raw = base64.b64decode(re.sub(r"\s+", "", value), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(
            "--image 必须是本地图片路径、图片 URL、Data URL 或有效 Base64。"
        ) from exc
    return _validated_image_upload(raw, "input", source="base64")


def describe_image_input(value):
    """为 dry-run/debug 描述输入，绝不返回 Base64 或图片字节。"""
    value = str(value)
    if value.startswith("data:"):
        return {"source": "data-url", "content": "<omitted>"}
    if re.match(r"^https?://", value, re.I):
        filename = Path(urllib.parse.urlparse(value).path).name or "input"
        return {"source": "url", "filename": filename, "url": "<omitted>"}
    try:
        path = Path(value)
        if path.is_file():
            return {
                "source": "local-file",
                "filename": path.name,
                "bytes": path.stat().st_size,
            }
    except OSError:
        pass
    return {"source": "base64", "content": "<omitted>"}

# ----------------------------------------------------------------------------- request builders


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
    model = validate_sub2_model(model)
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
    """把文本字段和内存图片编码为 multipart/form-data。"""
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

    file_items = [("image", item) for item in images]
    if mask:
        file_items.append(("mask", mask))
    for field_name, item in file_items:
        safe_name = item["filename"].replace('"', "_")
        add_line(f"--{boundary}")
        add_line(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"'
        )
        add_line(f"Content-Type: {item['mime']}")
        add_line()
        add_line(item["data"])

    add_line(f"--{boundary}--")
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def build_generation_body(model, prompt, options=None, extra_body=None):
    """构建 Sub2 OpenAI-compatible /images/generations JSON 请求。"""
    model = validate_sub2_model(model)
    options = dict(options or {})
    extra_body = dict(extra_body or {})
    body = {"model": model, "prompt": prompt, "n": options.get("n", 1)}

    if options.get("output_compression") is not None and options.get(
        "output_format"
    ) not in ("jpeg", "webp"):
        raise ValueError("output_compression 只适用于 output_format=jpeg 或 webp。")
    for name in (
        "size",
        "quality",
        "response_format",
        "output_format",
        "output_compression",
        "background",
        "moderation",
        "style",
        "user",
    ):
        if options.get(name) is not None:
            body[name] = options[name]

    # aspect_ratio 与 K 级分辨率通过中转站 extra_fields 扩展发送。
    extra = _relay_extra_fields(options)
    if extra:
        body["extra_fields"] = extra
    return deep_merge(extra_body, body)

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
            elif kl == "url" and isinstance(v, str) and v.strip():
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

def redact_text(value, secrets_to_hide=()):
    text = str(value)
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


def sanitize(obj, limit=180, secrets_to_hide=()):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if str(key).lower() in {"api_key", "apikey", "authorization", "x-api-key"}:
                out[key] = "***REDACTED***"
            else:
                out[key] = sanitize(value, limit, secrets_to_hide)
        return out
    if isinstance(obj, list):
        return [sanitize(v, limit, secrets_to_hide) for v in obj]
    if isinstance(obj, str):
        obj = redact_text(obj, secrets_to_hide)
        if obj.startswith("data:image") and ";base64," in obj[:128]:
            return "<data-url omitted>"
        compact = re.sub(r"\s+", "", obj)
        if len(compact) > 256 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
            return f"<base64 omitted: {len(compact)} chars>"
        if len(obj) > limit:
            return obj[:limit] + f"...<+{len(obj) - limit} chars>"
    return obj

# ----------------------------------------------------------------------------- 单 provider 执行

def run_one(prov, prompt, args, single_target=True):
    label = prov["name"]
    is_edit = bool(args.image)
    request_endpoint = EDIT_ENDPOINT if is_edit else GENERATION_ENDPOINT
    if args.mask and not is_edit:
        raise ValueError("--mask 只能与 --image 图片编辑一起使用。")

    if is_edit:
        url = join_url(prov["base_url"], request_endpoint)
        fields = build_edit_fields(
            prov["model"], prompt,
            options=prov["request_options"],
            extra_body=prov["extra_body"],
        )
        # --param 在编辑请求中覆盖 multipart 文本字段。
        fields = deep_merge(fields, args.param_body)
        if "model" in fields:
            fields["model"] = validate_sub2_model(fields["model"])
            if (
                prov["name"] == "banana"
                and fields["model"] == "gpt-image-gemini-pro"
                and not args.model
            ):
                raise ValueError("banana 的 Pro 模型必须由用户明确指定 --model。")
        if args.dry_run:
            request_data = None
            content_type = "multipart/form-data; boundary=<generated-at-send-time>"
            prepared_images = []
            prepared_mask = None
            input_debug = [describe_image_input(value) for value in args.image]
            mask_debug = describe_image_input(args.mask) if args.mask else None
        else:
            prepared_images = [load_image_input(value, args.timeout) for value in args.image]
            prepared_mask = load_image_input(args.mask, args.timeout) if args.mask else None
            request_data, content_type = build_multipart(
                fields, prepared_images, prepared_mask
            )
            input_debug = [
                {
                    "source": item["source"],
                    "filename": item["filename"],
                    "mime": item["mime"],
                    "bytes": len(item["data"]),
                }
                for item in prepared_images
            ]
            mask_debug = (
                {
                    "source": prepared_mask["source"],
                    "filename": prepared_mask["filename"],
                    "mime": prepared_mask["mime"],
                    "bytes": len(prepared_mask["data"]),
                }
                if prepared_mask else None
            )
        headers = build_headers(prov["api_key"], content_type)
        debug_body = {
            "fields": fields,
            "image": input_debug,
            "mask": mask_debug,
        }
    else:
        url = join_url(prov["base_url"], request_endpoint)
        headers = build_headers(prov["api_key"])
        body = build_generation_body(
            prov["model"], prompt,
            options=prov["request_options"],
            extra_body=prov["extra_body"],
        )
        # --param 是显式的低层请求体覆盖，优先级高于所有结构化参数。
        body = deep_merge(body, args.param_body)
        if "model" in body:
            body["model"] = validate_sub2_model(body["model"])
            if (
                prov["name"] == "banana"
                and body["model"] == "gpt-image-gemini-pro"
                and not args.model
            ):
                raise ValueError("banana 的 Pro 模型必须由用户明确指定 --model。")
        debug_body = body

    operation = "edit" if is_edit else "generate"
    print(
        f"[{label}] POST {url}  "
        f"(operation={operation}, model={prov['model']})"
    )
    if args.debug or args.dry_run:
        print(f"[{label}] 请求参数:")
        print(json.dumps(sanitize(debug_body, secrets_to_hide=(prov["api_key"],)), ensure_ascii=False, indent=2))
    if args.dry_run:
        print(f"[{label}] ✓ dry-run，未发送网络请求")
        return []

    if is_edit:
        status, raw = post_with_retries(
            lambda: http_post_bytes(url, headers, request_data, args.timeout),
            label=label,
        )
    else:
        status, raw = post_with_retries(
            lambda: http_post_json(url, headers, body, args.timeout),
            label=label,
        )

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = None

    if args.debug:
        print(f"[{label}] HTTP {status}")
        if parsed is not None:
            print(json.dumps(sanitize(parsed, secrets_to_hide=(prov["api_key"],)), ensure_ascii=False, indent=2))
        else:
            print(redact_text(raw.decode("utf-8", "replace")[:2000], (prov["api_key"],)))

    if status >= 300:
        snippet = redact_text(raw.decode("utf-8", "replace")[:500], (prov["api_key"],))
        print(f"[{label}] ✗ 请求失败 HTTP {status}: {snippet}")
        if status == 400 and "images endpoint requires an image model" in snippet.lower():
            print(
                f"[{label}] 检查 model：Sub2API Gemini 必须使用 "
                "gpt-image-gemini-flash 或 gpt-image-gemini-pro，不能发送原始 Gemini 模型名。"
            )
        return []

    if parsed is None:
        print(f"[{label}] ✗ 返回不是 JSON，无法解析。用 --debug 查看原始内容。")
        return []

    images = extract_images(parsed)
    if not images:
        print(f"[{label}] ✗ 没在返回里找到图片。用 --debug 查看响应结构。")
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
        description="跨平台 Sub2API 图片生成与编辑 CLI。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", nargs="?", help="生图提示词")
    p.add_argument("--provider", "-p", default="banana",
                   help="provider 名，逗号分隔可同时多个；'all' 表示配置里全部。默认 banana")
    p.add_argument("--image", "-i", action="append", default=[],
                   help="输入图：本地路径、图片 URL、Data URL 或 Base64；可重复")
    p.add_argument("--mask", help="编辑遮罩图；仅与 images mode 的 --image 一起使用")
    p.add_argument("--out", "-o", help="单张输出时的文件名")
    p.add_argument("--outdir", default="output", help="输出目录，默认 ./output")
    p.add_argument("--model", "-m", help="覆盖模型名")
    p.add_argument("--size", help="尺寸，如 1024x1024 或 1536x1024")
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
                    f"defaults={defaults}"
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
