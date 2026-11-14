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
import concurrent.futures as _futures
import datetime as _dt
import hashlib
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
ASYNC_GENERATION_ENDPOINT = "/images/generations/async"
ASYNC_EDIT_ENDPOINT = "/images/edits/async"
TASK_ENDPOINT = "/images/tasks"
ASYNC_SUCCESS_STATES = {"completed", "done", "succeeded", "success", "finished", "ready"}
ASYNC_FAILURE_STATES = {"failed", "cancelled", "canceled", "expired", "error"}

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
    # 官方 Images 兼容：resolution 用于超分分组，width/height 可成对提交；
    # 非 auto 的 size 优先于 width/height。
    "resolution",
    "width",
    "height",
)

GEMINI_IMAGE_SIZES = {"0.5K", "512", "1K", "2K", "4K"}
DEFAULT_PROVIDER = "image2"
DEFAULT_SUB2_IMAGE_MODEL = "gpt-image-2"
# provider → 默认模型。banana 系列是历史兼容别名（gpt-banana），不是真实模型，
# 保留可用但不再作为默认。
DEFAULT_MODEL_BY_PROVIDER = {
    "image2": "gpt-image-2",
    "banana": "gpt-image-gemini-flash",
}
# 已知图片模型：只用于「猜哪些能生图」和打印提示，不再拦截请求。
KNOWN_IMAGE_MODELS = {
    "gpt-image-gemini-flash",
    "gpt-image-gemini-pro",
    "gpt-image-2",
}
SUB2_MODEL_ALIASES = {
    "gemini-3.1-flash-image": "gpt-image-gemini-flash",
    "gemini-3-pro-image": "gpt-image-gemini-pro",
}
# 从 /models 返回里挑图片模型的启发式关键词。
IMAGE_MODEL_HINTS = (
    "image", "dall", "flux", "banana", "imagine", "seedream", "seedance",
    "imagen", "kolors", "wanx", "recraft", "ideogram", "sd3", "sdxl",
    "stable-diffusion", "midjourney", "hidream", "janus", "qwen-image",
)
# 明确排除的非图片模型关键词，避免把多模态文本模型当成生图模型。
NON_IMAGE_MODEL_HINTS = (
    "embedding", "embed", "tts", "whisper", "rerank", "moderation",
    "transcribe", "asr", "ocr",
    # 视频模型名里常带 imagine/image 字样（如 grok-imagine-video-1.5），必须排除
    "video", "seedance", "veo-", "sora", "kling", "hailuo",
)
# 自动择优的优先级：只放便宜/常规图片模型，Pro 级必须用户显式指定。
AUTO_MODEL_PREFERENCE = (
    "gpt-image-2",
    "gpt-image-gemini-flash",
)
MODELS_CACHE_NAME = ".genimg-models.json"
MODELS_CACHE_TTL = 600
MODELS_ENDPOINT = "/models"
# 429/503 是限流与不可用；502/504 是网关/上游超时，都值得退避重试。
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
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
# 落盘时用的规范后缀，按 MIME 反查（避免 .jpeg/.tif 这类别名）。
EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}

# ----------------------------------------------------------------------------- config

def normalize_sub2_model(model):
    """把已知 Gemini 上游名固定映射为 Sub2API image 别名。"""
    return SUB2_MODEL_ALIASES.get(model, model)


_WARNED_UNKNOWN_MODELS = set()


def validate_sub2_model(model):
    """把已知上游名归一化成中转别名；未知模型放行，可用性交给中转判断。

    本地只做两件事：别名翻译，以及对没见过的模型名提示一次（每进程一次）。
    真正的模型可用性由中转的 /models 与 model_not_found 决定。
    """
    if not model:
        raise ValueError("模型名为空。")
    normalized = normalize_sub2_model(str(model).strip())
    if normalized not in KNOWN_IMAGE_MODELS and normalized not in _WARNED_UNKNOWN_MODELS:
        _WARNED_UNKNOWN_MODELS.add(normalized)
        print(
            f"[model] 提示：{normalized} 不在已知图片模型列表内，照常发送请求；"
            "若中转返回 model_not_found，用 --list-models 查看账号可用模型。"
        )
    return normalized


def looks_like_image_model(name):
    """按名字猜测是否是生图模型；只用于自动择优和列表展示。"""
    lowered = str(name).lower()
    if any(bad in lowered for bad in NON_IMAGE_MODEL_HINTS):
        return False
    return any(hint in lowered for hint in IMAGE_MODEL_HINTS)


def pick_image_model(available, configured=None):
    """在账号可用模型里择优；没有列表时退回配置值。

    优先级：配置值（若列表里存在）> 常规图片模型优先级 > 列表里第一个像图片的
    > 配置值。Pro 级模型不参与自动选择，避免静默升价。
    """
    configured = validate_sub2_model(configured) if configured else None
    candidates = [m for m in (available or []) if looks_like_image_model(m)]
    if not available:
        return configured or DEFAULT_SUB2_IMAGE_MODEL
    if configured and configured in available:
        return configured
    for preferred in AUTO_MODEL_PREFERENCE:
        if preferred in candidates:
            return preferred
    if candidates:
        return candidates[0]
    return configured or DEFAULT_SUB2_IMAGE_MODEL

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
        raise ValueError(
            "缺少 IMAGE_API_BASE。先执行："
            "export IMAGE_API_BASE='https://你的中转站/v1'"
            "（必须以 /v1 结尾），再 export GENIMG_API_KEY='sk-xxx'。"
        )
    if not image_api_base.rstrip("/").endswith("/v1"):
        raise ValueError("IMAGE_API_BASE 必须包含并以 /v1 结尾。")
    if not api_key and not getattr(args, "dry_run", False):
        raise ValueError("缺少 GENIMG_API_KEY。")

    configured_model = cfg.get("model") or DEFAULT_MODEL_BY_PROVIDER.get(
        name, DEFAULT_SUB2_IMAGE_MODEL
    )
    available = getattr(args, "available_models", None)
    if args.model:
        # 显式指定：只做别名归一化，可用性交给中转判断。
        model = validate_sub2_model(args.model)
    else:
        configured_model = validate_sub2_model(configured_model)
        if name == "banana" and configured_model == "gpt-image-gemini-pro":
            raise ValueError(
                "banana 的 Pro 模型必须由用户明确指定："
                "--model gpt-image-gemini-pro。"
            )
        model = pick_image_model(available, configured_model)
        if available and model != configured_model:
            print(
                f"[{name}] 默认模型 {configured_model} 不在账号可用列表内，"
                f"自动改用 {model}；要指定请用 --model。"
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


# ----------------------------------------------------------------------------- model discovery


def models_cache_path(outdir=None):
    return Path(outdir or "output") / MODELS_CACHE_NAME


def _read_models_cache(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_models_cache(path, data):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def fetch_available_models(base_url, api_key, timeout=20):
    """GET /models，返回 (names, error)；失败不抛异常，交给调用方回退。"""
    url = join_url(base_url, MODELS_ENDPOINT)
    request = urllib.request.Request(url, headers=build_headers(api_key), method="GET")
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
        if isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
        else:
            name = str(item)
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            names.append(name)
    return names, None


def discover_models(base_url, api_key, outdir=None, refresh=False, timeout=20,
                    ttl=MODELS_CACHE_TTL):
    """带 TTL 缓存的模型发现；返回 (names, error, from_cache)。"""
    path = models_cache_path(outdir)
    cache_key = hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:12]
    now = time.time()
    if not refresh:
        cached = _read_models_cache(path).get(cache_key)
        if cached and now - float(cached.get("fetched_at") or 0) < ttl:
            models = cached.get("models")
            if models:
                return models, None, True
    names, error = fetch_available_models(base_url, api_key, timeout)
    if names is None:
        return None, error, False
    data = _read_models_cache(path)
    data[cache_key] = {"fetched_at": now, "base_url": base_url, "models": names}
    _write_models_cache(path, data)
    return names, None, False


def print_model_list(names, error=None, outdir=None):
    if names is None:
        print(f"拉取模型列表失败：{error}")
        print("可以直接用 --model NAME 指定模型，或检查 IMAGE_API_BASE / GENIMG_API_KEY。")
        return
    images = [n for n in names if looks_like_image_model(n)]
    print(f"账号可用模型 {len(names)} 个，其中疑似生图模型 {len(images)} 个：")
    for index, name in enumerate(images, 1):
        mark = "  ← 已知" if name in KNOWN_IMAGE_MODELS else ""
        print(f"  {index}) {name}{mark}")
    others = [n for n in names if n not in images]
    if others:
        shown = ", ".join(others[:12]) + ("…" if len(others) > 12 else "")
        print(f"其它 {len(others)} 个（按名字判断不像生图模型）：{shown}")
    print(f"缓存：{models_cache_path(outdir)}（--refresh-models 强制刷新）")


def interactive_pick_model(names):
    """终端交互选择；非 TTY（agent/管道）返回 None，由调用方自动择优。"""
    images = [n for n in (names or []) if looks_like_image_model(n)]
    if not images:
        return None
    if not sys.stdin.isatty():
        print("[model] 非交互环境，跳过手动选择；用 --model NAME 指定具体模型。")
        return None
    print("可用生图模型：")
    for index, name in enumerate(images, 1):
        print(f"  {index}) {name}")
    try:
        raw = input("选择编号或模型名（回车=自动择优）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(images):
        return images[int(raw) - 1]
    if raw in names:
        return raw
    print(f"[model] 没有匹配到 {raw}，改用自动择优。")
    return None


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


def http_get_bytes(url, timeout, headers=None):
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _same_origin(url, base_url):
    """判断产物 URL 是否就在中转自己域下（同源才允许带 Authorization）。"""
    try:
        target = urllib.parse.urlparse(url)
        base = urllib.parse.urlparse(base_url or "")
    except ValueError:
        return False
    return bool(target.hostname) and target.hostname.lower() == (base.hostname or "").lower()


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


def _probe_png(raw):
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _probe_gif(raw):
    if len(raw) < 10:
        return None
    return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")


def _probe_webp(raw):
    if len(raw) < 30:
        return None
    chunk = raw[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(raw[24:27], "little") + 1
        height = int.from_bytes(raw[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 ":
        if raw[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(raw[26:28], "little") & 0x3FFF
        height = int.from_bytes(raw[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L":
        if raw[20] != 0x2F:
            return None
        bits = int.from_bytes(raw[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _probe_bmp(raw):
    if len(raw) < 26:
        return None
    width = int.from_bytes(raw[18:22], "little", signed=True)
    height = int.from_bytes(raw[22:26], "little", signed=True)
    return abs(width), abs(height)


# SOF 段标记：C0-C3、C5-C7、C9-CB、CD-CF（跳过 DHT/JPG/DAC）。
_JPEG_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _probe_jpeg(raw):
    index = 2
    total = len(raw)
    while index < total - 9:
        if raw[index] != 0xFF:
            index += 1
            continue
        marker = raw[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > total:
            return None
        segment_length = int.from_bytes(raw[index + 2:index + 4], "big")
        if segment_length < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            height = int.from_bytes(raw[index + 5:index + 7], "big")
            width = int.from_bytes(raw[index + 7:index + 9], "big")
            return width, height
        index += 2 + segment_length
    return None


def probe_image_size(raw):
    """只读文件头解析宽高（PNG/JPEG/WebP/GIF/BMP），失败返回 (None, None)。

    用于遮罩尺寸校验和结果汇报；TIFF 等未覆盖的格式返回未知，不阻塞流程。
    """
    if not raw:
        return None, None
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            size = _probe_png(raw)
        elif raw[:3] == b"\xff\xd8\xff":
            size = _probe_jpeg(raw)
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            size = _probe_webp(raw)
        elif raw[:6] in (b"GIF87a", b"GIF89a"):
            size = _probe_gif(raw)
        elif raw[:2] == b"BM":
            size = _probe_bmp(raw)
        else:
            size = None
    except (IndexError, ValueError):
        size = None
    if not size:
        return None, None
    width, height = size
    if not width or not height:
        return None, None
    return int(width), int(height)


def describe_dimensions(width, height):
    return f"{width}x{height}" if width and height else "尺寸未知"


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
        safe_name = Path(safe_name).stem + EXT_BY_MIME[detected_mime]
    width, height = probe_image_size(raw)
    return {
        "filename": safe_name,
        "mime": detected_mime,
        "data": raw,
        "source": source,
        "width": width,
        "height": height,
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


def check_mask_dimensions(images, mask):
    """遮罩必须与第一张输入图同尺寸；无法解析时放行，只在能判断时拦截。"""
    if not mask or not images:
        return
    mask_w, mask_h = mask.get("width"), mask.get("height")
    base_w, base_h = images[0].get("width"), images[0].get("height")
    if not all((mask_w, mask_h, base_w, base_h)):
        return
    if (mask_w, mask_h) != (base_w, base_h):
        raise ValueError(
            f"遮罩尺寸 {mask_w}x{mask_h} 与第一张输入图 {base_w}x{base_h} 不一致；"
            "把遮罩裁到相同尺寸并保留 alpha 通道后重试。"
        )


def write_run_manifest(outdir, records, extra=None):
    """把本次运行写成 JSON 清单，便于复现与后续 agent 汇报。"""
    if not records:
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = Path(outdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "endpoint_base": os.environ.get("IMAGE_API_BASE", ""),
        "saved_total": sum(len(r.get("saved") or []) for r in records),
        "runs": records,
    }
    if extra:
        manifest.update(extra)
    path = target_dir / f"run-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path

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
        "resolution", "width", "height",
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
        "resolution",
        "width",
        "height",
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
    """递归扫描任意 JSON，抽出所有图片。返回 [('b64'|'url', payload), ...]。

    只按完整内容去重：同一 URL 被响应重复回显很常见，但不能因为前缀相同就
    吞掉真正不同的图片。
    """
    found = []
    _walk(obj, found, parent_key=None)
    seen, out = set(), []
    for kind, val in found:
        key = (kind, hashlib.sha256(val.encode("utf-8")).hexdigest())
        if key not in seen:
            seen.add(key)
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
    mime = sniff_input_mime(raw)
    return EXT_BY_MIME.get(mime, ".png")


def save_image(kind, payload, out_base, timeout, auth=None):
    """保存并校验图片字节；返回可直接写进运行清单的元信息。

    产物是中转自己域名下的图片时带上 Authorization（很多中转的存储做了防盗链）；
    第三方对象存储域名一律不带，避免把中转 Key 转发出去。
    """
    if kind == "b64":
        raw = base64.b64decode(clean_b64(payload))
        origin = "b64_json"
    else:
        headers = None
        if auth and auth[1] and _same_origin(payload, auth[0]):
            headers = {"Authorization": f"Bearer {auth[1]}"}
        try:
            raw = http_get_bytes(payload, timeout, headers)
        except urllib.error.HTTPError as error:
            host = urllib.parse.urlparse(payload).hostname or "?"
            third_party = not (auth and _same_origin(payload, auth[0]))
            hint = ""
            if third_party and error.code in (401, 403):
                hint = (
                    "；该链接由上游直出且需要上游鉴权，中转没有代理该产物，"
                    "客户端取不到：换用 gpt-image-2 等产物经中转代理的模型，"
                    "或让中转开启产物转发"
                )
            raise ValueError(
                f"下载图片失败 HTTP {error.code}（host={host}，"
                f"{'已带' if headers else '未带'}鉴权头{hint}）"
            ) from error
        origin = "url"
    if not raw:
        raise ValueError("图片内容为空")
    mime = sniff_input_mime(raw)
    if not mime:
        snippet = raw[:160].decode("utf-8", "replace").strip()
        raise ValueError(f"返回内容不是图片（可能是错误页、防盗链或过期链接）：{snippet}")
    ext = EXT_BY_MIME[mime]
    # 注意不能用 with_suffix：文件名里的点号会被当成后缀。
    # 例如 "poster-2.6-flare" 的 suffix 是 ".6-flare"，with_suffix(".png")
    # 会把它替换掉，得到 "poster-2.png"。这里改成直接拼接。
    out_base_text = str(out_base)
    if out_base_text.lower().endswith(ext):
        out_path = Path(out_base_text)
    else:
        out_path = Path(out_base_text + ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(raw)
    width, height = probe_image_size(raw)
    return {
        "path": str(out_path.resolve()),
        "bytes": len(raw),
        "mime": mime,
        "width": width,
        "height": height,
        "has_alpha": png_has_alpha(raw) if mime == "image/png" else None,
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
        "origin": origin,
    }


def png_has_alpha(raw):
    """只读 PNG 的 IHDR color type 判断是否带 alpha 通道（不解码像素）。"""
    if len(raw) < 26 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return raw[25] in (4, 6)


def verify_requested_params(requested, saved):
    """对比请求参数与实际产物，返回未生效项说明。

    中转普遍只实现了一部分参数：请求了 size/background/output_format 却拿回
    别的东西时，必须如实告知，不要让 agent 以为参数生效了。
    """
    problems = []
    if not saved:
        return problems
    first = saved[0]
    size = str(requested.get("size") or "").lower()
    if "x" in size:
        try:
            want_w, want_h = (int(part) for part in size.split("x", 1))
        except ValueError:
            want_w = want_h = None
        actual = (first.get("width"), first.get("height"))
        fmt_is_gif = str(requested.get("output_format")).lower() == "gif"
        if want_w and want_h and actual[0] and actual != (want_w, want_h):
            if fmt_is_gif:
                problems.append(
                    f"请求 size={size}，实际 {actual[0]}x{actual[1]}：GIF 直出会被压缩到 "
                    "约 414 像素长边，调大 size 不生效（要更大用视频转 GIF）"
                )
            else:
                problems.append(
                    f"请求 size={size}，实际 {actual[0]}x{actual[1]}：中转没有按 size 出图"
                )
    fmt = requested.get("output_format")
    if fmt:
        want_mime = {
            "png": "image/png", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif",
        }.get(fmt)
        if want_mime and first.get("mime") and first["mime"] != want_mime:
            problems.append(
                f"请求 output_format={fmt}，实际 {first['mime']}：中转忽略了输出格式"
            )
    if requested.get("background") == "transparent" and first.get("has_alpha") is False:
        problems.append("请求 background=transparent，实际是无 alpha 通道的 RGB：中转不支持透明底")
    if requested.get("quality") is not None:
        problems.append(
            f"quality={requested['quality']} 无法从产物验证；实测部分中转直接忽略该参数"
        )
    return problems

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

def explain_error(status, snippet, model):
    """把常见中转报错翻译成可执行的下一步，返回提示行列表。"""
    lowered = snippet.lower()
    hints = []
    if "async image tasks are not enabled" in lowered or (
        status == 404 and "async" in lowered
    ):
        hints.append(
            "该站点未启用异步图片任务（async image tasks are not enabled）："
            "去掉 --async 走同步 /images/generations；4K 实测 47–75s 可同步完成，"
            "不需要异步。"
        )
    if "images endpoint requires an image model" in lowered:
        hints.append(
            "model 必须是 Sub2 图片别名：gpt-image-gemini-flash / "
            "gpt-image-gemini-pro / gpt-image-2。"
        )
    if "model_not_found" in lowered or "model not found" in lowered:
        hints.append(
            f"中转没有开通 {model}：先跑 --list-models 看账号实际可用模型，"
            "再用 --model 指定，不要反复重试同一个模型。"
        )
    no_account_markers = (
        "no eligible", "not supported by any configured account",
        "no available compatible accounts", "无可用渠道",
    )
    no_account = any(marker in lowered for marker in no_account_markers)
    if no_account:
        hints.append(
            "中转分组里没有可用的上游账号（No eligible / not supported by any configured "
            "account）：这是中转侧的渠道配置问题，与你的 Key 无关。需要中转管理员给该分组接入"
            "上游账号；期间先用 --list-models 换一个渠道正常的模型。"
        )
    if "image_recovery_exhausted" in lowered or "交付恢复未完成" in snippet:
        hints.append(
            "图片已经生成但交付（回传/上传）失败 —— 不是模型问题，重试通常会重复扣费。"
            "中转提示用原任务编号查询交付结果：稍等再试，或让管理员按该任务号补发图片。"
        )
    if "insufficient" in lowered or "quota" in lowered or "余额" in snippet:
        hints.append("中转余额/额度不足；充值后再跑，不要靠换模型硬试。")
    if status in (401, 403):
        hints.append("检查 GENIMG_API_KEY 是否有效、是否属于该中转站。")
    if status == 413:
        hints.append("请求体过大：减少 --image 张数，或先压缩参考图。")
    if status in (502, 504):
        hints.append(
            "中转网关 502/504（上游过载或超时）：脚本已自动退避重试；仍失败说明上游持续"
            "异常，稍后再试，或降 --size / 稍后换时段。"
        )
    elif status in RETRYABLE_HTTP_STATUSES and not no_account:
        hints.append("已按 2/4/8 秒退避重试 3 次仍失败；稍后再试。")
    policy_markers = (
        "content_policy", "policy_violation", "policy_refusal", "moderation",
        "refusal", "inhaltsrichtlinien", "inappropriate", "审核",
    )
    if any(marker in lowered for marker in policy_markers):
        hints.append(
            "上游内容审核拒绝（部分模型策略更严，报错可能是外语原文）："
            "换一种描述或去掉可能敏感的词后重试，同一提示词不要连刷；"
            "需要稳定出图时用 --model gpt-image-2。"
        )
    return hints


# ----------------------------------------------------------------------------- 异步图片任务

IMAGE_TASK_LOG_NAME = ".genimg-async-tasks.jsonl"


def extract_image_task_id(payload):
    """从异步创建响应里找 task_id；兼容常见别名与嵌套结构。"""
    if not isinstance(payload, dict):
        return None
    for field in ("task_id", "id", "request_id", "job_id", "taskId", "task_ids"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0].strip()
    for wrapper in ("data", "task", "result", "output"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict):
            found = extract_image_task_id(inner)
            if found:
                return found
    return None


def http_get_json(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = None
    return status, parsed, raw


def poll_image_task(base_url, task_id, headers, args, label):
    """轮询 /images/tasks/{task_id}，成功时返回含图片的最终 payload。"""
    task_url = join_url(base_url, TASK_ENDPOINT) + "/" + task_id
    deadline = time.monotonic() + args.wait_timeout
    last_summary = None
    while True:
        status, payload, raw = http_get_json(task_url, headers, args.timeout)
        if status >= 300 or payload is None:
            snippet = redact_text(raw.decode("utf-8", "replace")[:300])
            raise RuntimeError(f"查询任务失败 HTTP {status}: {snippet}")
        state = str(payload.get("status", "unknown")).lower()
        progress = payload.get("progress")
        summary = (state, progress)
        if summary != last_summary:
            print(
                f"[{label}] 任务状态={state}, "
                f"进度={progress if progress is not None else '?'}%"
            )
            last_summary = summary
        if state in ASYNC_SUCCESS_STATES:
            return payload
        if state in ASYNC_FAILURE_STATES:
            detail = payload.get("error") or payload.get("message") or "未知错误"
            raise RuntimeError(f"图片任务 {state}: {detail}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待超过 {args.wait_timeout} 秒；任务仍可续查："
                f"python3 genimg.py --task-id {task_id}"
            )
        time.sleep(args.poll_interval)


def async_task_log_path(args):
    base = Path(args.out).parent if args.out else Path(args.outdir)
    return base / IMAGE_TASK_LOG_NAME


def record_async_task(path, entry):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"[task] ⚠ 任务记录写入失败：{error}", file=sys.stderr)


def read_async_tasks(path):
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


def latest_async_task(entries, only_pending=False):
    terminal = ASYNC_SUCCESS_STATES | ASYNC_FAILURE_STATES
    candidates = entries
    if only_pending:
        pending = [
            item for item in entries
            if str(item.get("status", "")).lower() not in terminal
        ]
        candidates = pending or entries
    return candidates[-1] if candidates else None


def print_async_tasks(entries, log_path):
    if not entries:
        print(f"本地没有异步任务记录：{log_path}")
        return
    print(f"异步任务记录：{log_path}（共 {len(entries)} 条）")
    for item in entries:
        print(
            f"  {item.get('created_at', '?')}  {item.get('task_id', '?')}  "
            f"status={item.get('status', '?')}  model={item.get('model', '?')}"
        )


def save_parsed_images(parsed, prov, args, record, label,
                       single_target=True, name_tag=""):
    """从任意响应里抽图并落盘；供同步、异步轮询、--task-id 续查共用。"""
    images = extract_images(parsed)
    if not images:
        print(f"[{label}] ✗ 没在返回里找到图片。用 --debug 查看响应结构。")
        record["error"] = "响应中没有图片"
        return record

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    multi = len(images) > 1
    for i, (kind, payload) in enumerate(images):
        suffix = f"_{i}" if multi else ""
        if args.out:
            base = Path(args.out)
            provider_tag = "" if single_target else f"_{label}"
            out_base = base.with_name(base.stem + provider_tag + name_tag + suffix)
        else:
            out_base = Path(args.outdir) / f"{label}{name_tag}_{stamp}{suffix}"
        try:
            info = save_image(
                kind, payload, out_base, args.timeout,
                auth=(prov["base_url"], prov["api_key"]),
            )
            print(
                f"[{label}] ✓ 保存 {info['path']}  "
                f"({describe_dimensions(info['width'], info['height'])}, "
                f"{info['mime']}, {info['bytes'] // 1024} KB, 来源 {kind})"
            )
            record["saved"].append(info)
        except Exception as e:
            print(f"[{label}] ✗ 保存失败: {e}")
            record["error"] = f"保存失败: {e}"

    warnings = verify_requested_params(prov["request_options"], record["saved"])
    if warnings:
        record["parameter_warnings"] = warnings
        for warning in warnings:
            print(f"[{label}] ⚠ {warning}")
    started = record.get("_started", time.monotonic())
    record["elapsed_s"] = round(time.monotonic() - started, 3)
    print(f"[{label}] 耗时 {record['elapsed_s']}s")
    return record


def read_prompt_file(path):
    """从文本文件读取多条提示词：一行一条，忽略空行和 # 注释行。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"--prompt-file 不存在：{path}")
    prompts = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        prompts.append(line)
    if not prompts:
        raise ValueError(f"--prompt-file 里没有有效提示词：{path}")
    return prompts



def run_one(prov, prompt, args, single_target=True, name_tag=""):
    """执行单个 provider 的一次生成/编辑，返回运行记录（含保存结果）。"""
    label = prov["name"]
    started = time.monotonic()
    is_edit = bool(args.image)
    request_endpoint = EDIT_ENDPOINT if is_edit else GENERATION_ENDPOINT
    if args.mask and not is_edit:
        raise ValueError("--mask 只能与 --image 图片编辑一起使用。")

    record = {
        "provider": label,
        "model": prov["model"],
        "operation": "edit" if is_edit else "generate",
        "prompt": prompt,
        "requested": {
            key: value
            for key, value in prov["request_options"].items()
            if value is not None
        },
        "saved": [],
        "error": None,
        "elapsed_s": 0.0,
    }

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
            input_debug = [describe_image_input(value) for value in args.image]
            mask_debug = describe_image_input(args.mask) if args.mask else None
        else:
            prepared_images = [load_image_input(value, args.timeout) for value in args.image]
            prepared_mask = load_image_input(args.mask, args.timeout) if args.mask else None
            check_mask_dimensions(prepared_images, prepared_mask)
            request_data, content_type = build_multipart(
                fields, prepared_images, prepared_mask
            )
            input_debug = [
                {
                    "source": item["source"],
                    "filename": item["filename"],
                    "mime": item["mime"],
                    "bytes": len(item["data"]),
                    "size": describe_dimensions(item["width"], item["height"]),
                }
                for item in prepared_images
            ]
            mask_debug = (
                {
                    "source": prepared_mask["source"],
                    "filename": prepared_mask["filename"],
                    "mime": prepared_mask["mime"],
                    "bytes": len(prepared_mask["data"]),
                    "size": describe_dimensions(
                        prepared_mask["width"], prepared_mask["height"]
                    ),
                }
                if prepared_mask else None
            )
        headers = build_headers(prov["api_key"], content_type)
        debug_body = {
            "fields": fields,
            "image": input_debug,
            "mask": mask_debug,
        }
        record["input_images"] = input_debug
        record["mask"] = mask_debug
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

    use_async = bool(getattr(args, "async_mode", False)) and not getattr(args, "task_id", None)
    if use_async:
        url = join_url(
            prov["base_url"],
            ASYNC_EDIT_ENDPOINT if is_edit else ASYNC_GENERATION_ENDPOINT,
        )
        record["async"] = True
    record["endpoint"] = url

    resume_id = getattr(args, "task_id", None)
    if resume_id:
        print(f"[{label}] 续查异步任务：{resume_id}")
        try:
            resumed = poll_image_task(prov["base_url"], resume_id, headers, args, label)
        except Exception as error:
            print(f"[{label}] ✗ {error}")
            record["error"] = str(error)
            return record
        record["task_id"] = resume_id
        record["async"] = True
        record["_started"] = started
        return save_parsed_images(
            resumed, prov, args, record, label, single_target, name_tag
        )

    operation = record["operation"]
    print(
        f"[{label}] POST {url}  "
        f"(operation={operation}, model={prov['model']})"
    )
    if args.debug or args.dry_run:
        print(f"[{label}] 请求参数:")
        print(json.dumps(sanitize(debug_body, secrets_to_hide=(prov["api_key"],)), ensure_ascii=False, indent=2))
    if args.dry_run:
        print(f"[{label}] ✓ dry-run，未发送网络请求")
        record["elapsed_s"] = round(time.monotonic() - started, 3)
        return record

    if is_edit:
        post_once = lambda: http_post_bytes(url, headers, request_data, args.timeout)
    else:
        post_once = lambda: http_post_json(url, headers, body, args.timeout)

    status, raw = post_with_retries(post_once, label=label)

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

    record["status"] = status
    record["elapsed_s"] = round(time.monotonic() - started, 3)

    if status >= 300:
        snippet = redact_text(raw.decode("utf-8", "replace")[:500], (prov["api_key"],))
        print(f"[{label}] ✗ 请求失败 HTTP {status}: {snippet}")
        for hint in explain_error(status, snippet, prov["model"]):
            print(f"[{label}] → {hint}")
        record["error"] = f"HTTP {status}: {snippet}"
        return record

    if parsed is None:
        print(f"[{label}] ✗ 返回不是 JSON，无法解析。用 --debug 查看原始内容。")
        record["error"] = "响应不是 JSON"
        return record

    if use_async:
        task_id = extract_image_task_id(parsed)
        if not task_id:
            print(f"[{label}] ✗ 异步创建响应里没有 task_id；用 --debug 看结构。")
            record["error"] = "异步响应缺少 task_id"
            return record
        print(f"[{label}] ✓ 异步任务已创建：{task_id}")
        record["task_id"] = task_id
        log_path = async_task_log_path(args)
        record_async_task(log_path, {
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "task_id": task_id, "status": "created",
            "model": prov["model"], "prompt": prompt,
        })
        print(f"[{label}] 已记录到 {log_path.resolve()}")
        if getattr(args, "no_wait", False):
            print(f"[{label}] 续查：python3 genimg.py --task-id {task_id}")
            return record
        try:
            parsed = poll_image_task(prov["base_url"], task_id, headers, args, label)
        except Exception as error:
            print(f"[{label}] ✗ {error}")
            record["error"] = str(error)
            record_async_task(log_path, {
                "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "task_id": task_id, "status": "pending", "error": str(error),
            })
            return record
        record_async_task(log_path, {
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "task_id": task_id, "status": "completed", "model": prov["model"],
        })

    images = extract_images(parsed)
    if not images:
        print(f"[{label}] ✗ 没在返回里找到图片。用 --debug 查看响应结构。")
        record["error"] = "响应中没有图片"
        return record

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    multi = len(images) > 1
    for i, (kind, payload) in enumerate(images):
        suffix = f"_{i}" if multi else ""
        if args.out:
            base = Path(args.out)
            provider_tag = "" if single_target else f"_{label}"
            out_base = base.with_name(base.stem + provider_tag + name_tag + suffix)
        else:
            out_base = Path(args.outdir) / f"{label}{name_tag}_{stamp}{suffix}"
        try:
            info = save_image(
                kind, payload, out_base, args.timeout,
                auth=(prov["base_url"], prov["api_key"]),
            )
            print(
                f"[{label}] ✓ 保存 {info['path']}  "
                f"({describe_dimensions(info['width'], info['height'])}, "
                f"{info['mime']}, {info['bytes'] // 1024} KB, 来源 {kind})"
            )
            record["saved"].append(info)
        except Exception as e:
            print(f"[{label}] ✗ 保存失败: {e}")
            record["error"] = f"保存失败: {e}"

    warnings = verify_requested_params(prov["request_options"], record["saved"])
    if warnings:
        record["parameter_warnings"] = warnings
        for warning in warnings:
            print(f"[{label}] ⚠ {warning}")
    record["elapsed_s"] = round(time.monotonic() - started, 3)
    print(f"[{label}] 耗时 {record['elapsed_s']}s")
    return record


# ----------------------------------------------------------------------------- main

def read_prompt_file(path):
    """从文本文件读取多条提示词：一行一条，忽略空行和 # 注释行。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"--prompt-file 不存在：{path}")
    prompts = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        prompts.append(line)
    if not prompts:
        raise ValueError(f"--prompt-file 里没有有效提示词：{path}")
    return prompts


def run_provider(name, config, args, prompt, single_target, name_tag):
    """解析单个 provider 并执行；所有异常都转成带 label 的失败记录。"""
    try:
        prov = resolve_provider(name, config, args)
    except ValueError as e:
        print(f"[{name}] ✗ {e}")
        return None
    try:
        return run_one(prov, prompt, args, single_target=single_target, name_tag=name_tag)
    except urllib.error.URLError as e:
        print(f"[{name}] ✗ 网络错误: {e}")
    except Exception as e:
        print(f"[{name}] ✗ 出错: {e}")
    return None


def run_all(names, config, args, prompt, name_tag=""):
    """按 --jobs 并发执行多个 provider，结果按 providers 声明顺序返回。"""
    single_target = len(names) == 1
    jobs = max(1, int(getattr(args, "jobs", 1) or 1))
    if jobs == 1 or len(names) == 1:
        results = [
            (name, run_provider(name, config, args, prompt, single_target, name_tag))
            for name in names
        ]
        return results

    ordered = {}
    with _futures.ThreadPoolExecutor(max_workers=min(jobs, len(names))) as pool:
        futures = {
            pool.submit(
                run_provider, name, config, args, prompt, single_target, name_tag
            ): name
            for name in names
        }
        for future in _futures.as_completed(futures):
            ordered[futures[future]] = future.result()
    return [(name, ordered.get(name)) for name in names]


def main(argv=None):
    configure_stdio()
    p = argparse.ArgumentParser(
        prog="genimg",
        description="跨平台 Sub2API 图片生成与编辑 CLI。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", nargs="?", help="生图提示词")
    p.add_argument("--prompt-file", dest="prompt_file",
                   help="从文件批量读取提示词，一行一条（忽略空行和 # 注释）")
    p.add_argument("--provider", "-p", default=DEFAULT_PROVIDER,
                   help=f"provider 名，逗号分隔可同时多个；'all' 表示配置里全部。"
                        f"默认 {DEFAULT_PROVIDER}（gpt-image-2）；banana 是兼容别名")
    p.add_argument("--image", "-i", action="append", default=[],
                   help="输入图：本地路径、图片 URL、Data URL 或 Base64；可重复")
    p.add_argument("--mask", help="编辑遮罩图；仅与 --image 图片编辑一起使用，尺寸须与第一张输入图一致")
    p.add_argument("--out", "-o", help="输出文件名；多图/多 provider 时自动加序号后缀")
    p.add_argument("--outdir", default="output", help="输出目录，默认 ./output")
    p.add_argument("--model", "-m", help="覆盖模型名")
    p.add_argument("--size", help="尺寸：1024x1024、1536x1024、16:9、1K/2K/4K、auto")
    p.add_argument("--resolution", help="超分分组用：2K/4K，配合 --size 的比例由服务端定目标尺寸")
    p.add_argument("--width", type=int, help="宽度；与 --height 成对，非 auto 的 --size 优先")
    p.add_argument("--height", type=int, help="高度；与 --width 成对")
    p.add_argument("--quality", help="质量，如 auto、low、high、2K、4K")
    p.add_argument("--response-format", dest="response_format", choices=["url", "b64_json"],
                   help="返回格式（DALL-E 2/3）：url 或 b64_json")
    p.add_argument("--aspect-ratio", dest="aspect_ratio", help="宽高比，如 9:16、16:9")
    p.add_argument("--output-format", dest="output_format",
                   choices=["png", "jpeg", "webp", "gif"],
                   help="输出编码：png、jpeg、webp；gif 需中转支持（2.6 部分渠道可出循环动图）")
    p.add_argument("--output-compression", dest="output_compression", type=int,
                   help="JPEG/WEBP 压缩质量 0-100（GPT Image）")
    p.add_argument("--background", choices=["auto", "transparent", "opaque"],
                   help="背景（GPT Image）：auto、transparent 或 opaque")
    p.add_argument("--moderation", choices=["auto", "low"], help="内容审核级别（GPT Image）")
    p.add_argument("--style", choices=["vivid", "natural"], help="风格（仅 DALL-E 3）")
    p.add_argument("--user", help="终端用户标识（OpenAI Images）")
    p.add_argument("--n", type=int, default=None, help="生成数量（images；默认 1）")
    p.add_argument("--jobs", type=int, default=1,
                   help="多 provider 并发数，默认 1（串行）")
    p.add_argument("--timeout", type=int, default=180, help="超时秒数，默认 180")
    p.add_argument("--config", help="指定 providers 配置文件")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="追加/覆盖自定义 JSON 请求字段，可重复，支持 a.b=value")
    p.add_argument("--list-models", dest="list_models", action="store_true",
                   help="向中转查询账号可用的生图模型（免费，不生成图片）")
    p.add_argument("--async", dest="async_mode", action="store_true",
                   help="走异步端点 /images/generations|edits/async 并轮询 /images/tasks")
    p.add_argument("--task-id", dest="task_id",
                   help="续查已有异步任务，不重新提交生成请求")
    p.add_argument("--resume-last", dest="resume_last", action="store_true",
                   help="续查本地记录里最近一个未完成的异步任务")
    p.add_argument("--list-tasks", dest="list_tasks", action="store_true",
                   help="列出本地记录的异步任务（不联网）")
    p.add_argument("--no-wait", dest="no_wait", action="store_true",
                   help="异步创建后立即返回 task_id，不轮询")
    p.add_argument("--poll-interval", dest="poll_interval", type=int, default=5,
                   help="异步轮询间隔秒数，默认 5")
    p.add_argument("--wait-timeout", dest="wait_timeout", type=int, default=900,
                   help="异步总等待上限秒数，默认 900")
    p.add_argument("--pick-model", dest="pick_model", action="store_true",
                   help="交互式选择模型（仅终端；非交互环境自动跳过）")
    p.add_argument("--refresh-models", dest="refresh_models", action="store_true",
                   help="忽略缓存的模型列表，重新向中转查询")
    p.add_argument("--no-discover", dest="no_discover", action="store_true",
                   help="不查询 /models，直接用配置的默认模型")
    p.add_argument("--no-manifest", dest="no_manifest", action="store_true",
                   help="不写 run-<时间戳>.json 运行清单")
    p.add_argument("--no-proxy", dest="no_proxy", action="store_true",
                   help="强制直连，忽略系统代理（本地中转站/代理拦截时用）")
    p.add_argument("--debug", action="store_true", help="打印原始返回结构")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="打印最终 URL/请求体但不发送请求")
    p.add_argument("--list", action="store_true", help="列出已配置的 provider")
    args = p.parse_args(argv)

    if args.n is not None and not 1 <= args.n <= 10:
        p.error("--n 必须在 1 到 10 之间")
    if args.jobs < 1:
        p.error("--jobs 必须大于等于 1")
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        p.error("--output-compression 必须在 0 到 100 之间")
    if args.prompt and args.prompt_file:
        p.error("提示词和 --prompt-file 只能二选一")
    try:
        args.param_body = parse_param_assignments(args.param)
    except ValueError as e:
        p.error(str(e))
    if args.mask and not args.image:
        p.error("--mask 必须与 --image 一起使用")

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

    if args.poll_interval < 1 or args.wait_timeout < 1:
        p.error("--poll-interval 与 --wait-timeout 必须大于 0")
    if args.task_id and args.resume_last:
        p.error("--task-id 与 --resume-last 只能二选一")
    if args.width is not None and args.height is None or \
            args.height is not None and args.width is None:
        p.error("--width 与 --height 必须成对使用")

    task_log = async_task_log_path(args)
    if args.list_tasks:
        print_async_tasks(read_async_tasks(task_log), task_log)
        return 0
    if args.resume_last:
        last = latest_async_task(read_async_tasks(task_log), only_pending=True)
        if not last or not last.get("task_id"):
            p.error(f"本地没有可续查的异步任务记录：{task_log}")
        args.task_id = last["task_id"]
        print(f"[task] 从本地记录续查：{args.task_id}")
    if args.task_id:
        args.async_mode = False
        if not getattr(args, "prompt", None) and not getattr(args, "prompt_file", None):
            # 续查不需要提示词，给个占位避免下面的必填校验
            args.prompt = "（续查任务，不提交新提示词）"
            args.no_discover = True

    # ---- 模型发现：默认自动查询（带缓存），失败就回退配置默认，绝阻塞出图
    args.available_models = None
    env_base = os.environ.get("IMAGE_API_BASE")
    env_key = os.environ.get("GENIMG_API_KEY")
    want_discovery = args.list_models or (
        not args.no_discover and env_base and (env_key or not args.dry_run)
    )
    if want_discovery and not env_base:
        print(
            "未设置 IMAGE_API_BASE，查不到模型列表。先执行：\n"
            "  export IMAGE_API_BASE='https://你的中转站/v1'\n"
            "  export GENIMG_API_KEY='sk-xxx'"
        )
        if args.list_models:
            return 1
        want_discovery = False
    if want_discovery:
        names_avail, discovery_error, from_cache = discover_models(
            env_base or "",
            env_key,
            outdir=args.outdir,
            refresh=args.refresh_models,
        )
        args.available_models = names_avail
        if args.list_models:
            print_model_list(names_avail, discovery_error, args.outdir)
            return 0 if names_avail is not None else 1
        if names_avail is None:
            print(f"[model] 查询模型列表失败（{discovery_error}），按配置默认模型继续。")
        else:
            salt = "（缓存）" if from_cache else ""
            images = [n for n in names_avail if looks_like_image_model(n)]
            print(
                f"[model] 账号可用模型 {len(names_avail)} 个，疑似生图 {len(images)} 个{salt}"
            )
            if args.pick_model:
                picked = interactive_pick_model(names_avail)
                if picked:
                    args.model = picked
                    print(f"[model] 手动选定：{picked}")
    elif args.list_models:
        print("需要 IMAGE_API_BASE 才能查询模型列表。")
        return 1

    if args.prompt_file:
        try:
            prompts = read_prompt_file(args.prompt_file)
        except ValueError as e:
            p.error(str(e))
    elif args.prompt:
        prompts = [args.prompt]
    else:
        p.error("缺少提示词。示例: python genimg.py \"一只柴犬\" --provider banana")

    # 解析目标 provider 列表
    if args.provider.lower() == "all":
        names = list(config.keys()) or ["banana"]
    else:
        names = [x.strip() for x in args.provider.split(",") if x.strip()]

    records = []
    completed_runs = 0
    for index, prompt in enumerate(prompts):
        name_tag = f"_p{index + 1}" if len(prompts) > 1 else ""
        if len(prompts) > 1:
            print(f"[batch] 第 {index + 1}/{len(prompts)} 条提示词")
        for name, record in run_all(names, config, args, prompt, name_tag):
            if record is None:
                continue
            records.append(record)
            if record.get("error") is None:
                completed_runs += 1

    saved = [item for record in records for item in (record.get("saved") or [])]
    manifest_path = None
    if records and not args.no_manifest and not args.dry_run:
        manifest_path = write_run_manifest(
            args.outdir,
            records,
            extra={"providers": names, "prompt_count": len(prompts)},
        )

    print("-" * 40)
    if args.dry_run:
        if completed_runs:
            print("dry-run 完成，未生成图片。")
            return 0
        print("dry-run 失败，没有构建出任何请求。")
        return 1
    created_tasks = [
        r.get("task_id") for r in records if r.get("task_id") and not r.get("saved")
    ]
    if args.no_wait and created_tasks and not saved:
        print(f"已创建 {len(created_tasks)} 个异步任务（--no-wait，未等待）：")
        for tid in created_tasks:
            print(f"  {tid}  续查：python3 genimg.py --task-id {tid}")
        return 0
    if saved:
        print(f"完成，共保存 {len(saved)} 张:")
        for item in saved:
            print(
                f"  {item['path']}  "
                f"({describe_dimensions(item['width'], item['height'])}, "
                f"{item['mime']}, {item['bytes'] // 1024} KB)"
            )
        if manifest_path:
            print(f"运行清单：{manifest_path}")
        return 0
    print("未生成任何图片。")
    for record in records:
        if record.get("error"):
            print(f"  - {record['provider']}: {record['error']}")
    if not os.environ.get("IMAGE_API_BASE") or not os.environ.get("GENIMG_API_KEY"):
        print(
            "配置未完成。设置 IMAGE_API_BASE 与 GENIMG_API_KEY 后，"
            "先跑 `--list-models` 免费自检。"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
