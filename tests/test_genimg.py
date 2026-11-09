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

    def test_image2_is_the_default_provider(self):
        config_path = Path(__file__).resolve().parents[1] / "providers.example.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("_comment", None)

        self.assertEqual(["image2", "banana"], list(config))
        self.assertEqual("gpt-image-2", config["image2"]["model"])
        self.assertEqual("gpt-image-gemini-flash", config["banana"]["model"])
        self.assertEqual("image2", genimg.DEFAULT_PROVIDER)
        self.assertEqual("gpt-image-2", genimg.DEFAULT_SUB2_IMAGE_MODEL)

    def test_default_provider_resolves_to_gpt_image_2(self):
        env = {"IMAGE_API_BASE": "https://sub2.example/v1", "GENIMG_API_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True):
            provider = genimg.resolve_provider("image2", {}, self.provider_args())
        self.assertEqual("gpt-image-2", provider["model"])

    def test_unknown_provider_name_does_not_become_a_model_name(self):
        # 以前会把 provider 名直接当模型名发给中转，现在回落到默认模型
        env = {"IMAGE_API_BASE": "https://sub2.example/v1", "GENIMG_API_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True):
            provider = genimg.resolve_provider("whatever", {}, self.provider_args())
        self.assertEqual("gpt-image-2", provider["model"])

    def test_banana_remains_available_as_compat_alias(self):
        env = {"IMAGE_API_BASE": "https://sub2.example/v1", "GENIMG_API_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True):
            provider = genimg.resolve_provider("banana", {}, self.provider_args())
        self.assertEqual("gpt-image-gemini-flash", provider["model"])

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

    def test_unknown_models_pass_through_instead_of_being_blocked(self):
        # 白名单已取消：本地只做别名归一化，可用性交给中转判断。
        self.assertEqual(
            "gpt-image-gemini-pro", genimg.validate_sub2_model("gemini-3-pro-image")
        )
        self.assertEqual(
            "gpt-image-2.5-flare", genimg.validate_sub2_model("gpt-image-2.5-flare")
        )
        self.assertEqual(
            "grok-imagine-image-2.0",
            genimg.validate_sub2_model("grok-imagine-image-2.0"),
        )

    def test_empty_model_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "模型名为空"):
            genimg.validate_sub2_model("")

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

    def test_gateway_502_is_retried(self):
        responses = iter([(502, b"bad gateway"), (200, b"ok")])
        sleeps = []

        status, raw = genimg.post_with_retries(
            lambda: next(responses), label="banana", sleep_fn=sleeps.append
        )

        self.assertEqual(200, status)
        self.assertEqual([2], sleeps)

    def test_gateway_502_hint_mentions_auto_retry(self):
        hints = genimg.explain_error(502, "origin_bad_gateway", "gpt-image-2")
        self.assertTrue(any("502" in h and "重试" in h for h in hints))

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


class ImageProbeTests(unittest.TestCase):
    @staticmethod
    def png(width, height):
        import struct
        import zlib

        row = b"\x00" + b"\x00\x00\x00" * width
        raw = row * height

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    def test_png_size_is_probed(self):
        self.assertEqual((640, 480), genimg.probe_image_size(self.png(640, 480)))

    def test_gif_size_is_probed(self):
        gif = b"GIF89a" + (320).to_bytes(2, "little") + (200).to_bytes(2, "little") + b"\x00" * 8
        self.assertEqual((320, 200), genimg.probe_image_size(gif))

    def test_jpeg_size_is_probed(self):
        jpeg = (
            b"\xff\xd8"
            + b"\xff\xe0" + (4).to_bytes(2, "big") + b"\x00\x00"
            + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
            + (1080).to_bytes(2, "big") + (1920).to_bytes(2, "big")
            + b"\x03" + b"\x00" * 9
        )
        self.assertEqual((1920, 1080), genimg.probe_image_size(jpeg))

    def test_unknown_bytes_return_none(self):
        self.assertEqual((None, None), genimg.probe_image_size(b"not an image"))

    def test_dimension_text_falls_back(self):
        self.assertEqual("尺寸未知", genimg.describe_dimensions(None, None))
        self.assertEqual("8x8", genimg.describe_dimensions(8, 8))


class MaskAndManifestTests(unittest.TestCase):
    def test_matching_mask_passes(self):
        images = [{"width": 512, "height": 512}]
        genimg.check_mask_dimensions(images, {"width": 512, "height": 512})

    def test_mismatched_mask_is_rejected(self):
        images = [{"width": 512, "height": 512}]
        with self.assertRaisesRegex(ValueError, "遮罩尺寸"):
            genimg.check_mask_dimensions(images, {"width": 256, "height": 512})

    def test_unparsable_mask_is_allowed(self):
        genimg.check_mask_dimensions([{"width": None, "height": None}], {"width": 1, "height": 1})

    def test_manifest_records_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [
                {
                    "provider": "banana",
                    "model": "gpt-image-gemini-flash",
                    "operation": "generate",
                    "prompt": "cat",
                    "saved": [{"path": "/tmp/a.png", "width": 8, "height": 8}],
                }
            ]
            path = genimg.write_run_manifest(tmp, records, extra={"providers": ["banana"]})
            data = json.loads(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(1, data["saved_total"])
        self.assertEqual(["banana"], data["providers"])
        self.assertEqual("banana", data["runs"][0]["provider"])


class SaveImageTests(unittest.TestCase):
    def test_b64_image_saved_with_probed_extension_and_meta(self):
        raw = ImageProbeTests.png(64, 32)
        encoded = genimg.base64.b64encode(raw).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            info = genimg.save_image("b64", encoded, Path(tmp) / "out.webp", timeout=5)

        self.assertTrue(info["path"].endswith(".png"))
        self.assertEqual((64, 32), (info["width"], info["height"]))
        self.assertEqual("image/png", info["mime"])
        self.assertEqual(16, len(info["sha256"]))

    def test_url_payload_that_is_html_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(genimg, "http_get_bytes", return_value=b"<html>403</html>"):
                with self.assertRaisesRegex(ValueError, "不是图片"):
                    genimg.save_image(
                        "url", "https://cdn.example/a.png", Path(tmp) / "a.png", timeout=5
                    )


class PromptFileTests(unittest.TestCase):
    def test_prompt_file_skips_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.txt"
            path.write_text("# 注释\n甲\n\n乙\n", encoding="utf-8")
            self.assertEqual(["甲", "乙"], genimg.read_prompt_file(path))

    def test_empty_prompt_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.txt"
            path.write_text("# 只有注释\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "没有有效提示词"):
                genimg.read_prompt_file(path)


class ErrorHintTests(unittest.TestCase):
    def test_model_error_hint(self):
        hints = genimg.explain_error(400, "model_not_found: gpt-image-2", "gpt-image-2")
        self.assertTrue(any("中转没有开通" in h for h in hints))

    def test_auth_hint(self):
        hints = genimg.explain_error(401, "invalid key", "gpt-image-2")
        self.assertTrue(any("GENIMG_API_KEY" in h for h in hints))

    def test_upstream_policy_refusal_hint_matches_foreign_text(self):
        hints = genimg.explain_error(
            400,
            '{"code":"upstream_policy_refusal_text","message":'
            '"das Bild verstößt gegen unsere Inhaltsrichtlinien"}',
            "gpt-image-2.5-flare",
        )
        self.assertTrue(any("内容审核" in h for h in hints))

    def test_retryable_hint(self):
        hints = genimg.explain_error(429, "busy", "gpt-image-gemini-flash")
        self.assertTrue(any("退避重试" in h for h in hints))


class ModelClassificationTests(unittest.TestCase):
    def test_image_models_are_recognised(self):
        for name in ("gpt-image-2", "gpt-image-gemini-flash", "gpt-image-2.5-flare",
                     "grok-imagine-image-2.0", "flux-pro-1.1", "seedream-4"):
            self.assertTrue(genimg.looks_like_image_model(name), name)

    def test_video_models_are_not_treated_as_image_models(self):
        for name in ("grok-imagine-video-1.5", "grok-imagine-video",
                     "video-ds-2.0-fast", "seedance-1.0-pro"):
            self.assertFalse(genimg.looks_like_image_model(name), name)

    def test_video_image_pairing_still_works(self):
        # 同一个厂商既有图片模型也有视频模型时，两类都要分开
        self.assertTrue(genimg.looks_like_image_model("grok-imagine-image-2.0"))
        self.assertFalse(genimg.looks_like_image_model("grok-imagine-video-1.5"))

    def test_text_and_embedding_models_are_not_images(self):
        for name in ("deepseek-v4-flash", "glm-5.3", "kimi-k3.1-pro",
                     "text-embedding-3-small", "tts-1-hd"):
            self.assertFalse(genimg.looks_like_image_model(name), name)


class ModelPickTests(unittest.TestCase):
    def test_no_list_falls_back_to_configured(self):
        self.assertEqual(
            "gpt-image-gemini-flash",
            genimg.pick_image_model(None, "gpt-image-gemini-flash"),
        )

    def test_configured_model_wins_when_available(self):
        self.assertEqual(
            "gpt-image-2.5-flare",
            genimg.pick_image_model(
                ["gpt-image-2", "gpt-image-2.5-flare"], "gpt-image-2.5-flare"
            ),
        )

    def test_unavailable_configured_model_is_replaced_by_preference(self):
        self.assertEqual(
            "gpt-image-2",
            genimg.pick_image_model(
                ["deepseek-v4-flash", "gpt-image-2.5-flare", "gpt-image-2"],
                "gpt-image-gemini-flash",
            ),
        )

    def test_first_image_model_is_used_when_no_preference_matches(self):
        self.assertEqual(
            "gpt-image-2.5-sunburst",
            genimg.pick_image_model(["glm-5.3", "gpt-image-2.5-sunburst"],
                                    "gpt-image-gemini-flash"),
        )

    def test_pro_is_not_chosen_automatically(self):
        picked = genimg.pick_image_model(
            ["gpt-image-gemini-pro", "gpt-image-2"], "gpt-image-gemini-flash"
        )
        self.assertEqual("gpt-image-2", picked)

    def test_all_text_models_keep_configured_value(self):
        self.assertEqual(
            "gpt-image-gemini-flash",
            genimg.pick_image_model(["deepseek-v4-flash", "glm-5.3"],
                                    "gpt-image-gemini-flash"),
        )


class ModelDiscoveryTests(unittest.TestCase):
    def fake_urlopen(self, payload):
        response = mock.MagicMock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__.return_value = response
        return response

    def test_fetch_parses_object_list(self):
        payload = {"data": [{"id": "gpt-image-2"}, {"id": "glm-5.3"}]}
        with mock.patch("urllib.request.urlopen", return_value=self.fake_urlopen(payload)):
            names, error = genimg.fetch_available_models("https://x/v1", "k", timeout=5)
        self.assertIsNone(error)
        self.assertEqual(["gpt-image-2", "glm-5.3"], names)

    def test_fetch_reports_http_error_without_raising(self):
        error = genimg.urllib.error.HTTPError("u", 401, "no", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            names, message = genimg.fetch_available_models("https://x/v1", "k", timeout=5)
        self.assertIsNone(names)
        self.assertIn("401", message)

    def test_discovery_caches_within_ttl(self):
        calls = []

        def fake_fetch(*args, **kwargs):
            calls.append(1)
            return ["gpt-image-2"], None

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            genimg, "fetch_available_models", side_effect=fake_fetch
        ):
            first = genimg.discover_models("https://x/v1", "k", outdir=tmp)
            second = genimg.discover_models("https://x/v1", "k", outdir=tmp)
            third = genimg.discover_models("https://x/v1", "k", outdir=tmp, refresh=True)

        self.assertEqual(2, len(calls))
        self.assertFalse(first[2])
        self.assertTrue(second[2])
        self.assertFalse(third[2])

    def test_discovery_failure_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            genimg, "fetch_available_models", return_value=(None, "HTTP 500")
        ):
            names, error, cached = genimg.discover_models("https://x/v1", "k", outdir=tmp)

        self.assertIsNone(names)
        self.assertEqual("HTTP 500", error)
        self.assertFalse(cached)


class SameOriginAuthTests(unittest.TestCase):
    def test_same_host_is_recognised(self):
        self.assertTrue(
            genimg._same_origin(
                "https://token.gufacoding.com/files/a.png",
                "https://token.gufacoding.com/v1",
            )
        )

    def test_third_party_host_is_not_same_origin(self):
        self.assertFalse(
            genimg._same_origin(
                "https://imgen.x.ai/images/a.png", "https://token.gufacoding.com/v1"
            )
        )
        self.assertFalse(genimg._same_origin("not a url", "https://x/v1"))

    def test_same_origin_download_sends_authorization(self):
        captured = {}

        def fake_get(url, timeout, headers=None):
            captured["headers"] = headers
            return b"\x89PNG\r\n\x1a\nbytes"

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            genimg, "http_get_bytes", side_effect=fake_get
        ):
            genimg.save_image(
                "url",
                "https://token.example/files/a.png",
                Path(tmp) / "a.png",
                timeout=5,
                auth=("https://token.example/v1", "sk-secret"),
            )

        self.assertEqual({"Authorization": "Bearer sk-secret"}, captured["headers"])

    def test_third_party_download_sends_no_authorization(self):
        captured = {}

        def fake_get(url, timeout, headers=None):
            captured["headers"] = headers
            return b"\x89PNG\r\n\x1a\nbytes"

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            genimg, "http_get_bytes", side_effect=fake_get
        ):
            genimg.save_image(
                "url",
                "https://imgen.x.ai/a.png",
                Path(tmp) / "a.png",
                timeout=5,
                auth=("https://token.example/v1", "sk-secret"),
            )

        self.assertIsNone(captured["headers"])


class ParameterVerificationTests(unittest.TestCase):
    def test_matching_params_are_quiet(self):
        problems = genimg.verify_requested_params(
            {"size": "1024x1024", "output_format": "png"},
            [{"width": 1024, "height": 1024, "mime": "image/png", "has_alpha": False}],
        )
        self.assertEqual([], problems)

    def test_size_mismatch_is_reported(self):
        problems = genimg.verify_requested_params(
            {"size": "1024x1024"},
            [{"width": 1254, "height": 1254, "mime": "image/png"}],
        )
        self.assertTrue(any("size" in item for item in problems))

    def test_format_mismatch_is_reported(self):
        problems = genimg.verify_requested_params(
            {"output_format": "webp"},
            [{"width": 8, "height": 8, "mime": "image/png"}],
        )
        self.assertTrue(any("输出格式" in item for item in problems))

    def test_unsupported_transparency_is_reported(self):
        problems = genimg.verify_requested_params(
            {"background": "transparent"},
            [{"width": 8, "height": 8, "mime": "image/png", "has_alpha": False}],
        )
        self.assertTrue(any("透明底" in item for item in problems))

    def test_real_alpha_is_quiet(self):
        problems = genimg.verify_requested_params(
            {"background": "transparent"},
            [{"width": 8, "height": 8, "mime": "image/png", "has_alpha": True}],
        )
        self.assertEqual([], problems)

    def test_quality_is_flagged_as_unverifiable(self):
        problems = genimg.verify_requested_params(
            {"quality": "low"}, [{"width": 8, "height": 8, "mime": "image/png"}]
        )
        self.assertTrue(any("quality" in item for item in problems))

    def test_png_alpha_probe_reads_color_type(self):
        opaque = ImageProbeTests.png(4, 4)
        self.assertFalse(genimg.png_has_alpha(opaque))
        rgba = bytearray(opaque)
        rgba[25] = 6
        self.assertTrue(genimg.png_has_alpha(bytes(rgba)))
        self.assertIsNone(genimg.png_has_alpha(b"not a png"))


class StdioEncodingTests(unittest.TestCase):
    def test_configure_stdio_is_idempotent(self):
        genimg.configure_stdio()
        genimg.configure_stdio()

    def test_streams_without_reconfigure_are_skipped(self):
        import io

        fake = io.StringIO()
        with mock.patch.object(genimg.sys, "stdout", fake), mock.patch.object(
            genimg.sys, "stderr", fake
        ):
            genimg.configure_stdio()

    def test_windows_hint_mentions_chcp(self):
        class LegacyStream:
            encoding = "cp1252"

            def reconfigure(self, **_kwargs):
                return None

        stream = LegacyStream()
        with mock.patch.object(genimg.sys, "stdout", stream), mock.patch.object(
            genimg.sys, "stderr", stream
        ), mock.patch.object(genimg.sys, "platform", "win32"), mock.patch(
            "builtins.print"
        ) as fake_print:
            genimg.configure_stdio()
        messages = " ".join(str(call) for call in fake_print.call_args_list)
        self.assertIn("chcp 65001", messages)


if __name__ == "__main__":
    unittest.main()
