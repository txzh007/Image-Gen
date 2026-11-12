#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""togif — 把视频片段转成高质量循环 GIF（零第三方依赖，调用系统 ffmpeg）。

用法：
  python3 togif.py input.mp4                       # 整段转 GIF
  python3 togif.py input.mp4 --start 6 --duration 3   # 截 6s 起 3 秒
  python3 togif.py input.mp4 --width 480 --fps 12 --colors 128

为什么不用 Images API 直接出 GIF：OpenAI Images 2.6 的循环 GIF 是 ChatGPT 应用功能，
不走 /v1/images/generations；实测中转对 format=gif 返回的仍是 PNG。视频转 GIF 是可控的替代。
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def find_ffmpeg():
    for name in ("ffmpeg", "ffmpeg.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def probe_duration(ffmpeg, source):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def build_filter(width, fps, colors, loop_forever):
    parts = [f"fps={fps}"]
    if width:
        parts.append(f"scale={width}:-1:flags=lanczos")
    # 先构图色板再套用，明显减少 GIF 的色带与抖动噪点
    palette = f"palettegen=max_colors={colors}:stats_mode=diff"
    use = "paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
    graph = ",".join(parts) + f",split[s0][s1];[s0]{palette}[p];[s1][p]{use}"
    return graph


def convert(ffmpeg, source, output, start=None, duration=None,
            width=640, fps=12, colors=128, loop_forever=True):
    graph = build_filter(width, fps, colors, loop_forever)
    command = [ffmpeg, "-v", "error", "-y"]
    if start is not None:
        command += ["-ss", str(start)]
    command += ["-i", str(source)]
    if duration is not None:
        command += ["-t", str(duration)]
    command += [
        "-filter_complex", graph,
        "-loop", "0" if loop_forever else "-1",
        "-f", "gif",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 失败（{completed.returncode}）：{completed.stderr.strip()[:400]}"
        )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="togif",
        description="把视频片段转成高质量循环 GIF（调用系统 ffmpeg）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python3 togif.py clip.mp4
  python3 togif.py clip.mp4 --start 6 --duration 3 --width 480 --fps 12
  python3 togif.py clip.mp4 --out banner.gif --colors 96
""",
    )
    parser.add_argument("source", help="输入视频路径")
    parser.add_argument("--out", "-o", help="输出 GIF 路径，默认与输入同目录同名 .gif")
    parser.add_argument("--start", type=float, help="起始秒数")
    parser.add_argument("--duration", type=float, help="截取时长（秒）")
    parser.add_argument("--width", type=int, default=640, help="输出宽度，默认 640；0 表示不缩放")
    parser.add_argument("--fps", type=int, default=12, help="帧率，默认 12（GIF 12-15 足够）")
    parser.add_argument("--colors", type=int, default=128, help="调色板颜色数，默认 128")
    parser.add_argument("--no-loop", action="store_true", help="不循环播放")
    args = parser.parse_args(argv)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("找不到 ffmpeg。安装后重试：brew install ffmpeg（macOS）"
              "或 choco install ffmpeg（Windows）")
        return 1

    source = Path(args.source)
    if not source.is_file():
        print(f"输入文件不存在：{source}")
        return 1

    total = probe_duration(ffmpeg, source)
    if total:
        print(f"[gif] 源视频 {source.name}：{total:.2f}s")

    if args.start is not None and total and args.start >= total:
        print(f"[gif] --start {args.start} 超出视频长度 {total:.2f}s")
        return 1
    if args.duration is not None and args.start is not None and total:
        if args.start + args.duration > total:
            print(f"[gif] 截取区间超出末尾，将截到 {total:.2f}s 为止")

    output = Path(args.out) if args.out else source.with_suffix(".gif")
    output.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"[gif] 转换中：width={args.width} fps={args.fps} colors={args.colors} "
        f"start={args.start} duration={args.duration} loop={'yes' if not args.no_loop else 'no'}"
    )
    try:
        convert(
            ffmpeg, source, output,
            start=args.start, duration=args.duration, width=args.width,
            fps=args.fps, colors=args.colors, loop_forever=not args.no_loop,
        )
    except RuntimeError as error:
        print(f"[gif] ✗ {error}")
        return 1

    size = output.stat().st_size
    print(f"[gif] ✓ 保存 {output.resolve()} ({size // 1024} KB)")
    if size > 8 * 1024 * 1024:
        print("[gif] 提示：文件偏大，试试 --width 480 --fps 10 --colors 96")
    return 0


if __name__ == "__main__":
    sys.exit(main())
