# -*- coding: utf-8 -*-
"""两个脚本共用的小工具。"""
import glob
import os
import shutil
import sys
from pathlib import Path


def find_tool(name):
    """找 ffmpeg / ffprobe：先找 PATH，再找 winget 的安装位置。"""
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(local, "Microsoft", "WinGet", "Links", name + ".exe")]
    candidates += glob.glob(os.path.join(
        local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", name + ".exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    sys.exit(f"找不到 {name}，请先双击运行 install.bat")


def transcript_path(video: Path) -> Path:
    return video.with_name(video.stem + ".transcript.json")


def fmt_clock(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def fmt_srt(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def video_arg():
    if len(sys.argv) < 2:
        print("用法：把视频文件拖到这个 .bat 图标上。")
        sys.exit(1)
    video = Path(sys.argv[1]).resolve()
    if not video.is_file():
        sys.exit(f"找不到文件：{video}")
    return video
