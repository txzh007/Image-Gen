# -*- coding: utf-8 -*-
"""端到端：用本地假中转真跑 genimg.py / genvideo.py 的命令行。

不联网、不需要真实 Key，但走完真实参数解析、HTTP、落盘、清单与任务记录。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_relay import MockRelay, make_png  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def run_cli(script, args, cwd, relay):
    env = dict(os.environ)
    env["IMAGE_API_BASE"] = relay.base_url
    env["GENIMG_API_KEY"] = relay.api_key
    proc = subprocess.run(
        [sys.executable, str(REPO / script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc


class GenimgEndToEndTests(unittest.TestCase):
    def test_text_to_image_writes_file_and_manifest(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["一只戴墨镜的柴犬", "--provider", "banana", "--out", "shots/dog.png"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            saved = Path(tmp) / "shots" / "dog.png"
            self.assertTrue(saved.is_file(), proc.stdout)
            self.assertEqual(b"\x89PNG\r\n\x1a\n", saved.read_bytes()[:8])

            manifests = list((Path(tmp) / "output").glob("run-*.json"))
            self.assertEqual(1, len(manifests))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(1, manifest["saved_total"])
            record = manifest["runs"][0]
            self.assertEqual("banana", record["provider"])
            self.assertEqual("generate", record["operation"])
            self.assertEqual(1024, record["saved"][0]["width"])
            self.assertEqual(1024, record["saved"][0]["height"])

    def test_size_and_n_are_sent_and_numbered(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["四格漫画", "--provider", "banana", "--size", "512x768", "--n", "2"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            files = sorted((Path(tmp) / "output").glob("banana_*.png"))
            self.assertEqual(2, len(files), proc.stdout)
            sent = relay.httpd.last_image_request
            self.assertEqual("512x768", sent["size"])
            self.assertEqual(2, sent["n"])

    def test_edit_upload_is_multipart_with_image_and_mask(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "input.png"
            mask = Path(tmp) / "mask.png"
            base.write_bytes(make_png(640, 480))
            mask.write_bytes(make_png(640, 480))
            proc = run_cli(
                "genimg.py",
                [
                    "只把背景换成夜景，主体不变",
                    "--provider", "banana",
                    "--image", str(base),
                    "--mask", str(mask),
                    "--out", "edited.png",
                ],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertTrue((Path(tmp) / "edited.png").is_file())
            posts = [r for r in relay.requests if r["method"] == "POST"]
            self.assertTrue(posts[-1]["path"].endswith("/images/edits"))
            self.assertIn("multipart/form-data", posts[-1]["content_type"])
            body = posts[-1]["body"]
            self.assertIn(b'name="image"; filename="input.png"', body)
            self.assertIn(b'name="mask"; filename="mask.png"', body)

    def test_mask_size_mismatch_is_rejected_before_upload(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "input.png"
            mask = Path(tmp) / "mask.png"
            base.write_bytes(make_png(640, 480))
            mask.write_bytes(make_png(320, 240))
            proc = run_cli(
                "genimg.py",
                ["换背景", "--image", str(base), "--mask", str(mask)],
                tmp,
                relay,
            )
            self.assertEqual(1, proc.returncode)
            self.assertIn("遮罩尺寸", proc.stdout)
            self.assertEqual([], [r for r in relay.requests if r["method"] == "POST"])

    def test_model_error_prints_actionable_hint(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py", ["FORCE_MODEL 测试", "--provider", "banana"], tmp, relay
            )
            self.assertEqual(1, proc.returncode)
            self.assertIn("model_not_found", proc.stdout)
            self.assertIn("中转没有开通", proc.stdout)

    def test_non_image_url_response_is_not_saved_as_png(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py", ["FORCE_URL 测试", "--provider", "banana"], tmp, relay
            )
            self.assertEqual(1, proc.returncode)
            self.assertIn("不是图片", proc.stdout)
            self.assertEqual([], list((Path(tmp) / "output").glob("*.png")))

    def test_prompt_file_batch_generates_one_image_per_prompt(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            prompt_file = Path(tmp) / "prompts.txt"
            prompt_file.write_text(
                "# 注释行忽略\n雨夜霓虹街头\n\n雪原日出\n", encoding="utf-8"
            )
            proc = run_cli(
                "genimg.py", ["--prompt-file", str(prompt_file), "--provider", "banana"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            files = sorted((Path(tmp) / "output").glob("banana_p*_*.png"))
            self.assertEqual(2, len(files), proc.stdout)
            self.assertTrue(any("_p1_" in f.name for f in files))
            self.assertTrue(any("_p2_" in f.name for f in files))

    def test_two_providers_with_jobs_run_concurrently(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["赛博朋克城市", "--provider", "banana,image2", "--jobs", "2"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertEqual(2, len(list((Path(tmp) / "output").glob("*.png"))))

    def test_dry_run_writes_no_manifest_and_sends_no_generation(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py", ["只检查", "--provider", "banana", "--dry-run"], tmp, relay
            )
            self.assertEqual(0, proc.returncode)
            self.assertEqual([], list((Path(tmp) / "output").glob("run-*.json")))
            self.assertEqual([], [r for r in relay.requests if r["method"] == "POST"])

    def test_banana_auto_switches_to_an_available_model(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("genimg.py", ["柴犬", "--provider", "banana"], tmp, relay)
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertIn("自动改用 gpt-image-2", proc.stdout)
            self.assertEqual("gpt-image-2", relay.httpd.last_image_request["model"])

    def test_explicit_model_outside_old_whitelist_is_sent(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["柴犬", "--provider", "image2", "--model", "gpt-image-2.5-flare"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertEqual(
                "gpt-image-2.5-flare", relay.httpd.last_image_request["model"]
            )

    def test_list_models_shows_image_candidates_without_generating(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("genimg.py", ["--list-models"], tmp, relay)
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertIn("疑似生图模型 2 个", proc.stdout)
            self.assertIn("gpt-image-2.5-flare", proc.stdout)
            self.assertEqual([], [r for r in relay.requests if r["method"] == "POST"])

    def test_no_discover_uses_static_default(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py", ["柴犬", "--provider", "banana", "--no-discover"], tmp, relay
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertEqual(
                [], [r for r in relay.requests if r["path"].endswith("/models")]
            )
            self.assertEqual(
                "gpt-image-gemini-flash", relay.httpd.last_image_request["model"]
            )

    def test_ignored_size_is_reported_back(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["FORCE_RANDOM_SIZE 测试", "--provider", "banana", "--size", "1024x1024"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertIn("中转没有按 size 出图", proc.stdout)
            manifest = json.loads(
                sorted((Path(tmp) / "output").glob("run-*.json"))[0].read_text("utf-8")
            )
            self.assertTrue(manifest["runs"][0].get("parameter_warnings"))

    def test_unsupported_transparent_background_is_reported(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genimg.py",
                ["测试", "--provider", "banana", "--background", "transparent"],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertIn("中转不支持透明底", proc.stdout)

    def test_model_list_is_cached_between_runs(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            run_cli("genimg.py", ["柴犬", "--provider", "banana"], tmp, relay)
            first = len([r for r in relay.requests if r["path"].endswith("/models")])
            run_cli("genimg.py", ["柴犬", "--provider", "banana"], tmp, relay)
            second = len([r for r in relay.requests if r["path"].endswith("/models")])
            self.assertEqual(1, first)
            self.assertEqual(1, second, "第二次应命中本地缓存，不再请求 /models")


class GenvideoEndToEndTests(unittest.TestCase):
    def test_create_poll_download_and_record_task(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "genvideo.py",
                [
                    "雨夜城市上空飞行",
                    "--seconds", "5",
                    "--poll-interval", "1",
                    "--out", "clips/result.mp4",
                ],
                tmp,
                relay,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            video = Path(tmp) / "clips" / "result.mp4"
            self.assertTrue(video.is_file(), proc.stdout)
            self.assertIn(b"ftyp", video.read_bytes()[:32])
            self.assertIn("媒体信息", proc.stdout)

            log_path = Path(tmp) / "clips" / ".genvideo-tasks.jsonl"
            entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual("created", entries[0]["status"])
            self.assertEqual("completed", entries[-1]["status"])
            self.assertEqual("task_mock_1", entries[-1]["task_id"])

    def test_list_tasks_reads_local_log_without_network(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "output" / ".genvideo-tasks.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "created_at": "2026-01-01T00:00:00",
                        "task_id": "task_abc",
                        "status": "created",
                        "model": "video-ds-2.0-fast",
                        "seconds": "5",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            proc = run_cli("genvideo.py", ["--list-tasks"], tmp, relay)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("task_abc", proc.stdout)
            self.assertEqual([], relay.requests)

    def test_resume_last_uses_recorded_task_id(self):
        with MockRelay() as relay, tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "output" / ".genvideo-tasks.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "created_at": "2026-01-01T00:00:00",
                        "task_id": "task_mock_1",
                        "status": "created",
                        "model": "video-ds-2.0-fast",
                        "seconds": "5",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            proc = run_cli(
                "genvideo.py", ["--resume-last", "--poll-interval", "1"], tmp, relay
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("从本地记录续查：task_mock_1", proc.stdout)
            posts = [r for r in relay.requests if r["method"] == "POST"]
            self.assertEqual([], posts)


if __name__ == "__main__":
    unittest.main()
