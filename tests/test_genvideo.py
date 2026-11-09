import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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

    def test_auto_upload_passes_service_api_key_from_environment(self):
        calls = []
        fake_upload = types.SimpleNamespace(
            upload_file=lambda path, service, api_key: (
                calls.append((path, service, api_key))
                or "https://example.com/uploaded.png"
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "input.png"
            image.write_bytes(b"test")
            with mock.patch.dict(sys.modules, {"upload": fake_upload}), mock.patch.dict(
                os.environ, {"IMGBB_API_KEY": "imgbb-test-key"}
            ):
                result = genvideo.validate_reference_urls(
                    "image", [str(image)], auto_upload="imgbb"
                )

        self.assertEqual(["https://example.com/uploaded.png"], result)
        self.assertEqual([(str(image), "imgbb", "imgbb-test-key")], calls)

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


class TaskLogTests(unittest.TestCase):
    def entry(self, **overrides):
        base = {
            "created_at": "2026-01-01T00:00:00",
            "task_id": "task_1",
            "status": "created",
        }
        base.update(overrides)
        return base

    def test_record_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sub" / ".genvideo-tasks.jsonl"
            self.assertTrue(genvideo.record_task(log, self.entry()))
            self.assertTrue(genvideo.record_task(log, self.entry(task_id="task_2")))
            entries = genvideo.read_task_entries(log)

        self.assertEqual(["task_1", "task_2"], [e["task_id"] for e in entries])

    def test_broken_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            log.write_text(
                json.dumps(self.entry()) + "\nnot-json\n", encoding="utf-8"
            )
            entries = genvideo.read_task_entries(log)

        self.assertEqual(1, len(entries))

    def test_latest_task_prefers_pending(self):
        entries = [
            self.entry(task_id="done", status="completed"),
            self.entry(task_id="running", status="in_progress"),
        ]
        self.assertEqual("running", genvideo.latest_task(entries, only_pending=True)["task_id"])
        self.assertEqual("running", genvideo.latest_task(entries)["task_id"])

    def test_latest_task_falls_back_when_all_terminal(self):
        entries = [self.entry(task_id="done", status="completed")]
        self.assertEqual("done", genvideo.latest_task(entries, only_pending=True)["task_id"])
        self.assertIsNone(genvideo.latest_task([]))

    def test_log_path_defaults_to_out_directory(self):
        args = types.SimpleNamespace(out="clips/a.mp4", log_file=None)
        self.assertEqual(Path("clips") / ".genvideo-tasks.jsonl", genvideo.task_log_path(args))
        args = types.SimpleNamespace(out=None, log_file="custom.jsonl")
        self.assertEqual(Path("custom.jsonl"), genvideo.task_log_path(args))


class MediaProbeTests(unittest.TestCase):
    def test_missing_ffprobe_is_skipped_quietly(self):
        with mock.patch("shutil.which", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "a.mp4"
                path.write_bytes(b"\x00\x00\x00\x18ftypisom")
                self.assertIsNone(genvideo.probe_media(path))
                self.assertIn("跳过媒体校验", genvideo.describe_media(path))

    def test_ffprobe_output_is_summarised(self):
        completed = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"streams": [{"width": 1280, "height": 720, "duration": "5.02", "codec_name": "h264"}]}
            ),
        )
        with mock.patch("shutil.which", return_value="/usr/bin/ffprobe"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "a.mp4"
                path.write_bytes(b"\x00\x00\x00\x18ftypisom")
                text = genvideo.describe_media(path)

        self.assertEqual("1280x720，5.0s，h264", text)


class VideoModelListingTests(unittest.TestCase):
    def test_video_models_are_recognised(self):
        for name in ("video-ds-2.0-fast", "video-ds-2.0", "seedance-1.0-pro",
                     "kling-v2", "veo-3", "sora-1", "hailuo-02"):
            self.assertTrue(genvideo.looks_like_video_model(name), name)

    def test_image_and_text_models_are_not_video(self):
        for name in ("gpt-image-2", "gpt-image-2.5-flare",
                     "grok-imagine-image-2.0", "deepseek-v4-flash",
                     "text-embedding-3-small", "tts-1"):
            self.assertFalse(genvideo.looks_like_video_model(name), name)


class CreateErrorHintTests(unittest.TestCase):
    def test_gateway_error_warns_about_duplicate_task(self):
        hint = genvideo.explain_create_error(502, None, b"error code: 502")
        self.assertIn("502", hint)
        self.assertIn("重复付费", hint)

    def test_model_not_found_points_to_list_models(self):
        hint = genvideo.explain_create_error(
            400,
            {"error": {"message": 'Model "x" is not available for this group'}},
            b"",
        )
        self.assertIn("--list-models", hint)

    def test_success_has_no_hint(self):
        self.assertIsNone(genvideo.explain_create_error(200, {"task_id": "x"}, b""))


if __name__ == "__main__":
    unittest.main()
