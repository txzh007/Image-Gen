import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import genimg


class RequestBodyTests(unittest.TestCase):
    @staticmethod
    def provider_args(**overrides):
        values = {
            "model": None,
            "dry_run": False,
        }
        values.update({name: None for name in genimg.REQUEST_OPTION_NAMES})
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_builtin_providers_are_banana_and_image2(self):
        config_path = Path(__file__).resolve().parents[1] / "providers.example.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("_comment", None)

        self.assertEqual(["banana", "image2"], list(config))
        self.assertEqual("gpt-image-gemini-flash", config["banana"]["model"])
        self.assertEqual("gpt-image-2", config["image2"]["model"])

    def test_images_request_includes_sub2_model_alias(self):
        body = genimg.build_generation_body(
            "gpt-image-gemini-flash",
            "cat",
            options={"n": 1},
        )

        self.assertEqual("gpt-image-gemini-flash", body["model"])
        self.assertEqual("cat", body["prompt"])

    def test_known_raw_gemini_names_are_never_sent(self):
        flash = genimg.build_generation_body(
            "gemini-3.1-flash-image", "cat", options={"n": 1}
        )
        pro = genimg.build_generation_body(
            "gemini-3-pro-image", "cat", options={"n": 1}
        )

        self.assertEqual("gpt-image-gemini-flash", flash["model"])
        self.assertEqual("gpt-image-gemini-pro", pro["model"])

    def test_legacy_environment_names_are_the_credential_source(self):
        config = {
            "banana": {
                "mode": "images",
                "model": "gpt-image-gemini-flash",
            }
        }
        env = {
            "IMAGE_API_BASE": "https://sub2.example/v1",
            "GENIMG_API_KEY": "legacy-key",
            "SUB2_BASE_URL": "https://ignored.example/v1",
            "SUB2_API_KEY": "ignored-key",
        }

        with mock.patch.dict("os.environ", env, clear=True):
            provider = genimg.resolve_provider(
                "banana", config, self.provider_args()
            )

        self.assertEqual("https://sub2.example/v1", provider["base_url"])
        self.assertEqual("legacy-key", provider["api_key"])

    def test_new_and_upstream_environment_names_are_not_used(self):
        env = {
            "SUB2_BASE_URL": "https://ignored.example/v1",
            "SUB2_API_KEY": "ignored-key",
            "OPENAI_API_KEY": "upstream-key",
        }

        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaisesRegex(ValueError, "IMAGE_API_BASE"):
                genimg.resolve_provider(
                    "banana", {"banana": {"mode": "images"}}, self.provider_args()
                )

    def test_only_declared_sub2_image_models_are_allowed(self):
        self.assertEqual(
            "gpt-image-gemini-pro",
            genimg.validate_sub2_model("gemini-3-pro-image"),
        )
        with self.assertRaisesRegex(ValueError, "不支持的图片模型"):
            genimg.validate_sub2_model("unknown-upstream-model")

    def test_banana_pro_requires_explicit_model_argument(self):
        env = {
            "IMAGE_API_BASE": "https://sub2.example/v1",
            "GENIMG_API_KEY": "sub2-key",
        }
        config = {"banana": {"model": "gpt-image-gemini-pro"}}

        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaisesRegex(ValueError, "明确指定"):
                genimg.resolve_provider("banana", config, self.provider_args())
            provider = genimg.resolve_provider(
                "banana",
                config,
                self.provider_args(model="gpt-image-gemini-pro"),
            )

        self.assertEqual("gpt-image-gemini-pro", provider["model"])

    def test_image_api_base_requires_v1(self):
        env = {
            "IMAGE_API_BASE": "https://sub2.example",
            "GENIMG_API_KEY": "sub2-key",
        }

        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaisesRegex(ValueError, "/v1"):
                genimg.resolve_provider(
                    "banana",
                    {"banana": {"mode": "images"}},
                    self.provider_args(),
                )

    def test_images_uses_standard_and_relay_options(self):
        body = genimg.build_generation_body(
            "gpt-image-2",
            "poster",
            options={
                "size": "1536x1024",
                "quality": "4K",
                "aspect_ratio": "16:9",
                "n": 2,
                "output_format": "webp",
                "background": "transparent",
            },
        )

        self.assertEqual("1536x1024", body["size"])
        self.assertEqual(2, body["n"])
        self.assertEqual("webp", body["output_format"])
        self.assertEqual("transparent", body["background"])
        self.assertEqual("16:9", body["extra_fields"]["google"]["image_config"]["aspect_ratio"])
        self.assertEqual("4K", body["extra_fields"]["google"]["image_config"]["image_size"])

    def test_param_assignments_parse_json_and_dotted_paths(self):
        result = genimg.parse_param_assignments(
            ["seed=42", "enabled=true", "google.image_config.image_size=\"2K\""]
        )

        self.assertEqual(42, result["seed"])
        self.assertIs(True, result["enabled"])
        self.assertEqual("2K", result["google"]["image_config"]["image_size"])

    def test_structured_options_override_provider_extra_body(self):
        body = genimg.build_generation_body(
            "gpt-image-2",
            "poster",
            options={"size": "1536x1024", "n": 1},
            extra_body={"size": "1024x1024", "seed": 7},
        )

        self.assertEqual("1536x1024", body["size"])
        self.assertEqual(7, body["seed"])

    def test_edit_fields_include_standard_and_relay_options(self):
        fields = genimg.build_edit_fields(
            "gpt-image-gemini-pro",
            "edit",
            options={"quality": "4K", "aspect_ratio": "16:9", "n": 1},
        )

        self.assertEqual("gpt-image-gemini-pro", fields["model"])
        self.assertEqual("4K", fields["quality"])
        self.assertEqual("16:9", fields["extra_fields"]["aspect_ratio"])
        image_config = fields["extra_fields"]["google"]["image_config"]
        self.assertEqual("4K", image_config["image_size"])

    def test_multipart_uses_repeated_image_and_mask_fields(self):
        image_one = {
            "filename": "input.png",
            "mime": "image/png",
            "data": b"image-one",
        }
        image_two = {
            "filename": "reference.jpg",
            "mime": "image/jpeg",
            "data": b"image-two",
        }
        mask = {
            "filename": "mask.png",
            "mime": "image/png",
            "data": b"mask-bytes",
        }

        body, content_type = genimg.build_multipart(
            {"model": "gpt-image-2", "prompt": "edit", "n": 1},
            [image_one, image_two],
            mask,
        )

        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertEqual(2, body.count(b'name="image"; filename='))
        self.assertNotIn(b'name="image[]"', body)
        self.assertIn(b'name="image"; filename="input.png"', body)
        self.assertIn(b'name="image"; filename="reference.jpg"', body)
        self.assertIn(b'name="mask"; filename="mask.png"', body)
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gpt-image-2", body)

    def test_local_file_is_loaded_into_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "input.png"
            raw = b"\x89PNG\r\n\x1a\nimage-bytes"
            image.write_bytes(raw)

            loaded = genimg.load_image_input(str(image))

        self.assertEqual("local-file", loaded["source"])
        self.assertEqual("image/png", loaded["mime"])
        self.assertEqual(raw, loaded["data"])

    def test_data_url_and_raw_base64_are_decoded_in_memory(self):
        raw = b"\xff\xd8\xffjpeg-bytes"
        encoded = genimg.base64.b64encode(raw).decode("ascii")

        data_url = genimg.load_image_input(f"data:image/jpeg;base64,{encoded}")
        raw_base64 = genimg.load_image_input(encoded)

        self.assertEqual("data-url", data_url["source"])
        self.assertEqual("base64", raw_base64["source"])
        self.assertEqual(raw, data_url["data"])
        self.assertEqual(raw, raw_base64["data"])

    def test_url_is_downloaded_and_mime_validated(self):
        response = mock.MagicMock()
        response.headers.get_content_type.return_value = "image/png"
        response.read.return_value = b"\x89PNG\r\n\x1a\nurl-image"
        response.geturl.return_value = "https://cdn.example/input.png"
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response):
            loaded = genimg.load_image_input("https://example.com/image", timeout=5)

        self.assertEqual("url", loaded["source"])
        self.assertEqual("image/png", loaded["mime"])
        self.assertEqual("input.png", loaded["filename"])

    def test_url_rejects_non_image_mime(self):
        response = mock.MagicMock()
        response.headers.get_content_type.return_value = "text/html"
        response.read.return_value = b"\x89PNG\r\n\x1a\nurl-image"
        response.geturl.return_value = "https://example.com/input.png"
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "MIME"):
                genimg.load_image_input("https://example.com/input.png")

    def test_invalid_image_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "本地图片路径"):
            genimg.load_image_input("not-an-image")

    def test_multipart_headers_use_relay_compatible_user_agent(self):
        headers = genimg.build_headers(
            "secret",
            "multipart/form-data; boundary=test",
        )

        self.assertEqual("*/*", headers["Accept"])
        self.assertTrue(headers["User-Agent"].startswith("curl/"))
        self.assertEqual("Bearer secret", headers["Authorization"])

    def test_retry_429_and_503_uses_fixed_backoff_without_model_switch(self):
        responses = iter([(429, b"busy"), (503, b"busy"), (200, b"ok")])
        sleeps = []

        status, raw = genimg.post_with_retries(
            lambda: next(responses),
            label="banana",
            sleep_fn=sleeps.append,
        )

        self.assertEqual(200, status)
        self.assertEqual(b"ok", raw)
        self.assertEqual([2, 4], sleeps)

    def test_retry_stops_after_three_retries(self):
        calls = []
        sleeps = []

        def post_once():
            calls.append(1)
            return 503, b"busy"

        status, _ = genimg.post_with_retries(
            post_once,
            label="banana",
            sleep_fn=sleeps.append,
        )

        self.assertEqual(503, status)
        self.assertEqual(4, len(calls))
        self.assertEqual([2, 4, 8], sleeps)

    def test_non_retryable_status_is_not_retried(self):
        calls = []

        status, _ = genimg.post_with_retries(
            lambda: (calls.append(1) and None) or (400, b"bad request"),
            sleep_fn=lambda _: self.fail("400 不应退避重试"),
        )

        self.assertEqual(400, status)
        self.assertEqual(1, len(calls))

    def test_sanitize_redacts_api_key(self):
        secret = "sk-sub2-secret"
        cleaned = genimg.sanitize(
            {"authorization": f"Bearer {secret}", "message": f"key={secret}"},
            secrets_to_hide=(secret,),
        )

        rendered = json.dumps(cleaned)
        self.assertNotIn(secret, rendered)
        self.assertIn("***REDACTED***", rendered)

    def test_sanitize_never_prints_base64_or_data_url(self):
        encoded = "A" * 400
        cleaned = genimg.sanitize(
            {"data_url": f"data:image/png;base64,{encoded}", "raw": encoded}
        )

        rendered = json.dumps(cleaned)
        self.assertNotIn(encoded, rendered)
        self.assertIn("omitted", rendered)

    def test_extract_images_supports_url_and_b64_json(self):
        found = genimg.extract_images(
            {
                "data": [
                    {"url": ""},
                    {"url": "https://example.com/a.png"},
                    {"b64_json": "YWJj"},
                ]
            }
        )

        self.assertEqual(
            [("url", "https://example.com/a.png"), ("b64", "YWJj")],
            found,
        )


if __name__ == "__main__":
    unittest.main()
