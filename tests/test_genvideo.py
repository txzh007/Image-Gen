import tempfile
import unittest
from pathlib import Path

import genvideo


class VideoRequestTests(unittest.TestCase):
    def test_build_video_body_with_multimedia_references(self):
        body = genvideo.build_video_body(
            "video-ds-2.0-fast",
            "cinematic cat",
            seconds=10,
            aspect_ratio="16:9",
            images=["https://example.com/cat.png"],
            videos=["https://example.com/motion.mp4"],
            audios=["https://example.com/music.mp3"],
        )

        self.assertEqual("10", body["seconds"])
        self.assertEqual("16:9", body["aspect_ratio"])
        self.assertEqual(["https://example.com/cat.png"], body["images"])
        self.assertEqual(["https://example.com/motion.mp4"], body["videos"])
        self.assertEqual(["https://example.com/music.mp3"], body["audios"])

    def test_reference_must_be_public_url(self):
        with self.assertRaisesRegex(ValueError, "公网"):
            genvideo.build_video_body(
                "video-ds-2.0-fast",
                "cat",
                images=["./cat.png"],
            )

    def test_extract_task_id_accepts_both_shapes(self):
        self.assertEqual("task-a", genvideo.extract_task_id({"task_id": "task-a"}))
        self.assertEqual("task-b", genvideo.extract_task_id({"id": "task-b"}))

    def test_extract_video_url_accepts_result_shapes(self):
        self.assertEqual(
            "https://example.com/a.mp4",
            genvideo.extract_video_url(
                {"result": {"video_url": "https://example.com/a.mp4"}}
            ),
        )
        self.assertEqual(
            "https://example.com/b.mp4",
            genvideo.extract_video_url(
                {"result": {"resultUrls": ["https://example.com/b.mp4"]}}
            ),
        )

    def test_param_assignments_support_nested_json_values(self):
        result = genvideo.parse_param_assignments(
            ["seed=42", "config.watermark=false", "resolution=\"720p\""]
        )

        self.assertEqual(42, result["seed"])
        self.assertIs(False, result["config"]["watermark"])
        self.assertEqual("720p", result["resolution"])

    def test_mp4_detection_checks_ftyp_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp) / "valid.mp4"
            invalid = Path(tmp) / "invalid.mp4"
            valid.write_bytes(b"\x00\x00\x00\x18ftypisom")
            invalid.write_text("error code: 502", encoding="utf-8")

            self.assertTrue(genvideo._looks_like_mp4(valid))
            self.assertFalse(genvideo._looks_like_mp4(invalid))


if __name__ == "__main__":
    unittest.main()
