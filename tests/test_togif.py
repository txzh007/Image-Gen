# -*- coding: utf-8 -*-
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import togif  # noqa: E402


class FilterTests(unittest.TestCase):
    def test_filter_uses_palettegen_and_paletteuse(self):
        graph = togif.build_filter(480, 12, 128, True)
        self.assertIn("fps=12", graph)
        self.assertIn("scale=480:-1", graph)
        self.assertIn("palettegen=max_colors=128", graph)
        self.assertIn("paletteuse=", graph)

    def test_zero_width_skips_scaling(self):
        graph = togif.build_filter(0, 10, 96, True)
        # paletteuse 里的 bayer_scale 也含 "scale="，这里看缩放滤镜本身
        self.assertNotIn("lanczos", graph)
        self.assertIn("max_colors=96", graph)


class MainTests(unittest.TestCase):
    def test_missing_ffmpeg_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.mp4"
            src.write_bytes(b"x")
            with mock.patch.object(togif, "find_ffmpeg", return_value=None):
                code = togif.main([str(src)])
        self.assertEqual(1, code)

    def test_missing_source_is_reported(self):
        with mock.patch.object(togif, "find_ffmpeg", return_value="/usr/bin/ffmpeg"):
            code = togif.main(["/nonexistent/clip.mp4"])
        self.assertEqual(1, code)

    def test_convert_called_with_expected_args(self):
        calls = {}

        def fake_convert(ffmpeg, source, output, **kwargs):
            calls["output"] = str(output)
            calls["kwargs"] = kwargs
            Path(output).write_bytes(b"GIF89a" + b"\x00" * 32)
            return output

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(b"x")
            with mock.patch.object(togif, "find_ffmpeg", return_value="ffmpeg"), \
                 mock.patch.object(togif, "probe_duration", return_value=10.0), \
                 mock.patch.object(togif, "convert", side_effect=fake_convert):
                code = togif.main([str(src), "--start", "5", "--duration", "3",
                                   "--width", "480", "--fps", "12", "--colors", "96"])
        self.assertEqual(0, code)
        self.assertTrue(calls["output"].endswith("clip.gif"))
        self.assertEqual(5.0, calls["kwargs"]["start"])
        self.assertEqual(3.0, calls["kwargs"]["duration"])
        self.assertEqual(480, calls["kwargs"]["width"])
        self.assertEqual(96, calls["kwargs"]["colors"])

    def test_start_beyond_duration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(b"x")
            with mock.patch.object(togif, "find_ffmpeg", return_value="ffmpeg"), \
                 mock.patch.object(togif, "probe_duration", return_value=5.0):
                code = togif.main([str(src), "--start", "9"])
        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
