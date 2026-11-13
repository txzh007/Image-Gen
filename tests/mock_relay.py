#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地假中转：用于在没有真实 Key 的情况下跑通 genimg/genvideo 全链路。

只依赖标准库。用法：

    from mock_relay import MockRelay

    with MockRelay() as relay:
        os.environ["IMAGE_API_BASE"] = relay.base_url
        os.environ["GENIMG_API_KEY"] = relay.api_key

也可以直接启动：

    python3 tests/mock_relay.py --port 18080
"""

import argparse
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 256

# 默认 /models 返回：文本模型 + 图片模型混排，且不含 banana 的默认模型，
# 这样能验证「自动择优」而不是「恰好命中默认」。
DEFAULT_MODELS = [
    "deepseek-v4-flash",
    "glm-5.3",
    "gpt-image-2.5-flare",
    "gpt-image-2",
    "text-embedding-3-small",
]


def make_png(width, height, color=(90, 122, 200)):
    """生成指定尺寸的合法 PNG（纯色），用来验证尺寸探测与落盘。"""
    row = bytes(color) * width
    raw = b"".join(b"\x00" + row for _ in range(height))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def parse_multipart(body, content_type):
    """极简 multipart 解析：返回 {name: [(filename, payload), ...]}。"""
    marker = "boundary="
    if marker not in content_type:
        return {}
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    delimiter = b"--" + boundary.encode()
    fields = {}
    for part in body.split(delimiter):
        if not part or part in (b"--\r\n", b"--"):
            continue
        part = part.lstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        head, payload = part.split(b"\r\n\r\n", 1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        text = head.decode("utf-8", "replace")
        if "name=" not in text:
            continue
        name = text.split('name="', 1)[1].split('"', 1)[0]
        filename = ""
        if 'filename="' in text:
            filename = text.split('filename="', 1)[1].split('"', 1)[0]
        fields.setdefault(name, []).append((filename, payload))
    return fields


def parse_size(size_text, default=(1024, 1024)):
    if not size_text or "x" not in str(size_text):
        return default
    try:
        width, height = str(size_text).lower().split("x", 1)
        return int(width), int(height)
    except ValueError:
        return default


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockRelay/1.0"

    # 测试里需要看请求体，关掉默认日志噪音。
    def log_message(self, fmt, *args):
        if self.server.verbose:
            print("[mock-relay] " + fmt % args)

    # ---------------------------------------------------------------- helpers
    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, entry):
        self.server.requests.append(entry)

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        self._record({"method": "GET", "path": path})
        # 中转常见前缀是 /v1，路由匹配时忽略它。
        if path.startswith("/v1/"):
            path = path[3:]
        if path.endswith("/content") and "/videos/" in path:
            self._send(200, FAKE_MP4, "video/mp4")
            return
        if path.startswith("/videos/"):
            task_id = path.rsplit("/", 1)[-1]
            state = self.server.poll_counts.get(task_id, 0)
            self.server.poll_counts[task_id] = state + 1
            if state < 1:
                self._send(200, {"task_id": task_id, "status": "in_progress", "progress": 40})
            else:
                self._send(200, {"task_id": task_id, "status": "completed", "progress": 100})
            return
        if path.startswith("/images/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            count = self.server.poll_counts.get(task_id, 0)
            self.server.poll_counts[task_id] = count + 1
            if count < 1:
                self._send(200, {"task_id": task_id, "status": "in_progress", "progress": 50})
            else:
                import base64 as _b64
                self._send(200, {
                    "task_id": task_id, "status": "completed", "progress": 100,
                    "data": [{"b64_json": _b64.b64encode(make_png(64, 32)).decode()}],
                })
            return
        if path == "/models":
            models = self.server.models
            if models is None:
                self._send(500, {"error": {"message": "models unavailable"}})
                return
            self._send(200, {"object": "list", "data": [{"id": n, "object": "model"} for n in models]})
            return
        if path == "/not-image":
            self._send(200, b"<html>403 hotlink denied</html>", "text/html")
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()
        content_type = self.headers.get("Content-Type", "")
        self._record(
            {"method": "POST", "path": path, "content_type": content_type, "body": body}
        )

        if path.endswith("/images/generations/async"):
            self._send(200, {"task_id": "img_task_mock_1", "status": "queued"})
            return

        if path.endswith("/images/generations"):
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._send(400, {"error": {"message": "invalid json"}})
                return
            self._handle_images(payload)
            return

        if path.endswith("/images/edits"):
            fields = parse_multipart(body, content_type)
            payload = {
                key: values[0][1].decode("utf-8", "replace")
                for key, values in fields.items()
                if values and not values[0][0]
            }
            payload["_files"] = {
                key: [name for name, _ in values] for key, values in fields.items() if values[0][0]
            }
            self._handle_images(payload)
            return

        if path.endswith("/videos"):
            payload = json.loads(body.decode("utf-8")) if body else {}
            task_id = "task_mock_1"
            self.server.poll_counts[task_id] = 0
            self.server.last_video_request = payload
            self._send(200, {"task_id": task_id, "status": "queued"})
            return

        self._send(404, {"error": {"message": "not found"}})

    # ---------------------------------------------------------------- images
    def _handle_images(self, payload):
        prompt = str(payload.get("prompt", ""))
        model = str(payload.get("model", ""))

        if "FORCE_429" in prompt:
            self._send(429, {"error": {"message": "rate limited"}})
            return
        if "FORCE_MODEL" in prompt:
            self._send(400, {"error": {"message": "model_not_found: " + model}})
            return
        if "FORCE_POLICY" in prompt:
            self._send(
                400,
                {"error": {"message": "content_policy_violation: rejected by moderation"}},
            )
            return
        if "FORCE_URL" in prompt:
            host = self.server.server_address
            self._send(
                200,
                {
                    "created": 1,
                    "data": [
                        {"url": f"http://127.0.0.1:{host[1]}/not-image"},
                    ],
                },
            )
            return

        width, height = parse_size(payload.get("size"))
        if "FORCE_RANDOM_SIZE" in prompt:
            width, height = 1254, 1254
        count = max(1, int(payload.get("n") or 1))
        import base64 as _b64

        self.server.last_image_request = payload
        self._send(
            200,
            {
                "created": 1,
                "data": [
                    {
                        "b64_json": _b64.b64encode(
                            make_png(width, height, color=(60 + index * 40, 122, 200))
                        ).decode("ascii")
                    }
                    for index in range(count)
                ],
            },
        )


class MockRelay:
    """上下文管理器：起一个假中转，暴露 base_url / api_key。"""

    def __init__(self, host="127.0.0.1", port=0, verbose=False, models=None):
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.models = list(DEFAULT_MODELS) if models is None else list(models)
        self.httpd.requests = []
        self.httpd.poll_counts = {}
        self.httpd.verbose = verbose
        self.httpd.last_image_request = None
        self.httpd.last_video_request = None
        self._thread = None

    @property
    def base_url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def api_key(self):
        return "sk-mock-relay-key"

    @property
    def requests(self):
        return self.httpd.requests

    def __enter__(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        return False


def main():
    parser = argparse.ArgumentParser(description="启动本地假中转（测试用）")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    relay = MockRelay(port=args.port, verbose=True)
    with relay:
        print(f"mock relay: {relay.base_url}")
        print(f"export IMAGE_API_BASE={relay.base_url}")
        print(f"export GENIMG_API_KEY={relay.api_key}")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("\n停止")


if __name__ == "__main__":
    main()
