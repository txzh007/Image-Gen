import json
import tempfile
import unittest
from pathlib import Path

import genimg


class RequestBodyTests(unittest.TestCase):
    def test_builtin_providers_are_banana_and_image2(self):
        config_path = Path(__file__).resolve().parents[1] / "providers.example.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("_comment", None)

        self.assertEqual(["banana", "image2"], list(config))
        self.assertEqual("gemini-3-pro-image", config["banana"]["model"])
        self.assertEqual("gpt-image-2", config["image2"]["model"])
        self.assertEqual("images", config["banana"]["mode"])
        self.assertEqual("images", config["image2"]["mode"])
        self.assertEqual("chat", config["banana"]["edit_mode"])
        self.assertEqual("/chat/completions", config["banana"]["edit_endpoint"])
        self.assertEqual("images", config["image2"]["edit_mode"])
        self.assertEqual("/images/edits", config["image2"]["edit_endpoint"])

    def test_images_uses_standard_and_relay_options(self):
        body, ignored = genimg.build_body(
            "images",
            "gpt-image-2",
            "poster",
            [],
            options={
                "size": "1536x1024",
                "quality": "4K",
                "aspect_ratio": "16:9",
                "n": 2,
                "output_format": "webp",
                "background": "transparent",
            },
        )

        self.assertEqual([], ignored)
        self.assertEqual("1536x1024", body["size"])
        self.assertEqual(2, body["n"])
        self.assertEqual("webp", body["output_format"])
        self.assertEqual("transparent", body["background"])
        self.assertEqual("16:9", body["extra_fields"]["google"]["image_config"]["aspect_ratio"])
        self.assertEqual("4K", body["extra_fields"]["google"]["image_config"]["image_size"])

    def test_gemini_maps_ratio_and_resolution(self):
        body, ignored = genimg.build_body(
            "gemini",
            "gemini-image",
            "poster",
            [],
            options={"aspect_ratio": "9:16", "quality": "4k", "n": 1},
        )

        self.assertEqual([], ignored)
        image = body["generationConfig"]["responseFormat"]["image"]
        self.assertEqual({"aspectRatio": "9:16", "imageSize": "4K"}, image)

    def test_chat_reports_unmapped_options_and_accepts_extra_body(self):
        body, ignored = genimg.build_body(
            "chat",
            "relay-image",
            "poster",
            [],
            options={"quality": "high", "n": 3},
            extra_body={"image_config": {"quality": "high"}},
        )

        self.assertEqual(["quality"], ignored)
        self.assertEqual(3, body["n"])
        self.assertEqual("high", body["image_config"]["quality"])

    def test_param_assignments_parse_json_and_dotted_paths(self):
        result = genimg.parse_param_assignments(
            ["seed=42", "enabled=true", "google.image_config.image_size=\"2K\""]
        )

        self.assertEqual(42, result["seed"])
        self.assertIs(True, result["enabled"])
        self.assertEqual("2K", result["google"]["image_config"]["image_size"])

    def test_structured_options_override_provider_extra_body(self):
        body, _ = genimg.build_body(
            "images",
            "gpt-image-2",
            "poster",
            [],
            options={"size": "1536x1024", "n": 1},
            extra_body={"size": "1024x1024", "seed": 7},
        )

        self.assertEqual("1536x1024", body["size"])
        self.assertEqual(7, body["seed"])

    def test_edit_fields_include_standard_and_relay_options(self):
        fields = genimg.build_edit_fields(
            "gemini-3-pro-image",
            "edit",
            options={"quality": "4K", "aspect_ratio": "16:9", "n": 1},
        )

        self.assertEqual("gemini-3-pro-image", fields["model"])
        self.assertEqual("4K", fields["quality"])
        self.assertEqual("16:9", fields["extra_fields"]["aspect_ratio"])
        image_config = fields["extra_fields"]["google"]["image_config"]
        self.assertEqual("4K", image_config["image_size"])

    def test_multipart_uses_image_array_and_mask_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "input.png"
            mask = Path(tmp) / "mask.png"
            image.write_bytes(b"image-bytes")
            mask.write_bytes(b"mask-bytes")

            body, content_type = genimg.build_multipart(
                {"model": "gpt-image-2", "prompt": "edit", "n": 1},
                [image],
                mask,
            )

        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="image[]"; filename="input.png"', body)
        self.assertIn(b'name="mask"; filename="mask.png"', body)
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gpt-image-2", body)

    def test_multipart_rejects_missing_image(self):
        with self.assertRaisesRegex(ValueError, "输入图片不存在"):
            genimg.build_multipart({"model": "gpt-image-2"}, ["missing.png"])

    def test_multipart_headers_use_relay_compatible_user_agent(self):
        headers = genimg.build_headers(
            "images",
            "secret",
            "multipart/form-data; boundary=test",
        )

        self.assertEqual("*/*", headers["Accept"])
        self.assertTrue(headers["User-Agent"].startswith("curl/"))
        self.assertEqual("Bearer secret", headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
