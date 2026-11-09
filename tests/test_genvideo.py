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

    def test_extract_task_id_accepts_request_id_and_nested(self):
        self.assertEqual(
            "req-1", genvideo.extract_task_id({"request_id": "req-1"})
        )
        self.assertEqual(
            "vid-9", genvideo.extract_task_id({"data": {"video_id": "vid-9"}})
        )
        self.assertEqual(
            "out-3", genvideo.extract_task_id({"result": {"task_id": "out-3"}})
        )
        self.assertIsNone(genvideo.extract_task_id({"unrelated": 1}))

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

    def test_no_eligible_account_is_explained(self):
        hint = genvideo.explain_create_error(
            503,
            {"error": {"message": "No eligible Grok media accounts",
                       "type": "grok_media_no_eligible_account"}},
            b"",
        )
        self.assertIn("上游账号", hint)

    def test_success_has_no_hint(self):
        self.assertIsNone(genvideo.explain_create_error(200, {"task_id": "x"}, b""))


class OpenAIVideoProtocolTests(unittest.TestCase):
    """严格 OpenAI Videos API：size + input_reference + 4/8/12 秒。"""

    def test_ratio_maps_to_openai_size(self):
        body = genvideo.build_openai_video_body(None, "rain city", aspect_ratio="9:16")
        self.assertEqual("sora-2", body["model"])
        self.assertEqual("720x1280", body["size"])
        self.assertEqual("4", body["seconds"])
        self.assertNotIn("aspect_ratio", body)

    def test_explicit_size_wins(self):
        body = genvideo.build_openai_video_body("sora-2-pro", "x", size="1792x1024")
        self.assertEqual("1792x1024", body["size"])
        self.assertEqual("sora-2-pro", body["model"])

    def test_unknown_size_rejected(self):
        with self.assertRaisesRegex(ValueError, "size 只支持"):
            genvideo.build_openai_video_body(None, "x", size="1920x1080")

    def test_unsupported_ratio_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有 1:1"):
            genvideo.build_openai_video_body(None, "x", aspect_ratio="1:1")

    def test_only_4_8_12_seconds_allowed(self):
        with self.assertRaisesRegex(ValueError, "4/8/12"):
            genvideo.build_openai_video_body(None, "x", seconds="5")
        body = genvideo.build_openai_video_body(None, "x", seconds="12")
        self.assertEqual("12", body["seconds"])

    def test_single_image_becomes_input_reference(self):
        body = genvideo.build_openai_video_body(
            None, "x", images=["https://example.com/a.png"]
        )
        self.assertEqual("https://example.com/a.png", body["input_reference"])

    def test_multiple_images_rejected(self):
        with self.assertRaisesRegex(ValueError, "只接受一张"):
            genvideo.build_openai_video_body(
                None, "x",
                images=["https://example.com/a.png", "https://example.com/b.png"],
            )

    def test_video_and_audio_references_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持视频/音频"):
            genvideo.build_openai_video_body(
                None, "x", videos=["https://example.com/a.mp4"]
            )


class SuccessStateTests(unittest.TestCase):
    def test_done_and_completed_are_both_success(self):
        # xAI/Grok 媒体渠道返回 done；OpenAI 返回 completed
        self.assertIn("done", genvideo.SUCCESS_STATES)
        self.assertIn("completed", genvideo.SUCCESS_STATES)

    def test_wait_for_task_returns_on_done_state(self):
        args = types.SimpleNamespace(
            wait_timeout=30, poll_interval=1, request_timeout=5, debug=False
        )
        payloads = [
            {"status": "in_progress", "progress": 40},
            {"status": "done", "progress": 100},
        ]

        def fake_http_json(method, url, headers, body=None, timeout=60):
            data = payloads.pop(0)
            return 200, data, b""

        with mock.patch.object(genvideo, "http_json", side_effect=fake_http_json):
            result = genvideo.wait_for_task(
                "https://x/v1", "/videos", "task-1", {}, args
            )
        self.assertEqual("done", result["status"])

    def test_failure_states_still_raise(self):
        args = types.SimpleNamespace(
            wait_timeout=30, poll_interval=1, request_timeout=5, debug=False
        )
        with mock.patch.object(
            genvideo, "http_json",
            return_value=(200, {"status": "failed", "error": "boom"}, b""),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                genvideo.wait_for_task("https://x/v1", "/videos", "t", {}, args)


class UsageRecordingTests(unittest.TestCase):
    def test_completed_entry_keeps_upstream_usage(self):
        # 这里只验证字段被保留：record_task 是纯 append，直接看写入内容
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "tasks.jsonl"
            genvideo.record_task(
                log,
                {"task_id": "t1", "status": "completed",
                 "usage": {"cost_in_usd_ticks": 2500000000}},
            )
            entry = genvideo.read_task_entries(log)[0]
        self.assertEqual(2500000000, entry["usage"]["cost_in_usd_ticks"])


class DurationResolutionTests(unittest.TestCase):
    """实测：duration(数字) + resolution 会直接改变输出分辨率与计费。"""

    def test_duration_replaces_seconds_as_number(self):
        body = genvideo.build_video_body(
            "grok-imagine-video", "x", duration=5, resolution="720p"
        )
        self.assertEqual(5, body["duration"])
        self.assertIsInstance(body["duration"], int)
        self.assertNotIn("seconds", body)

    def test_resolution_replaces_aspect_ratio(self):
        body = genvideo.build_video_body(
            "grok-imagine-video", "x", duration=5, resolution="720p"
        )
        self.assertEqual("720p", body["resolution"])
        self.assertNotIn("aspect_ratio", body)

    def test_legacy_fields_still_default(self):
        body = genvideo.build_video_body("video-ds-2.0-fast", "x")
        self.assertEqual("5", body["seconds"])
        self.assertEqual("16:9", body["aspect_ratio"])
        self.assertNotIn("duration", body)
        self.assertNotIn("resolution", body)

    def test_duration_only_keeps_aspect_ratio(self):
        body = genvideo.build_video_body("m", "x", duration=8)
        self.assertEqual(8, body["duration"])
        self.assertEqual("16:9", body["aspect_ratio"])


if __name__ == "__main__":
    unittest.main()
