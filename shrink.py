# -*- coding: utf-8 -*-
"""把视频压缩到 30MB 以内，方便上传给网页版 Claude（时间轴不变，剪辑方案能直接套回原片）。
太长压不进 30MB 的，自动切成几段，每段都在 30MB 以内。"""
import math
import subprocess
import sys
from pathlib import Path

from common import find_tool, video_arg

LIMIT_MB = 27          # 留一点余量，网页上限 30MB
AUDIO_KBPS = 96
MIN_VIDEO_KBPS = 600   # 再低画面就糊得看不清了，宁可切段
MAX_VIDEO_KBPS = 2500

FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def duration(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def encode(src, dst, start, length, vkbps):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if length:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
            "-c:v", "libx264", "-preset", "medium",
            "-b:v", f"{vkbps}k", "-maxrate", f"{int(vkbps * 1.3)}k", "-bufsize", f"{vkbps * 2}k",
            "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
            "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)


def main():
    src = video_arg()
    total = duration(src)
    budget_kbps = LIMIT_MB * 8 * 1024 / total - AUDIO_KBPS
    parts = 1
    if budget_kbps < MIN_VIDEO_KBPS:
        parts = math.ceil(total * (MIN_VIDEO_KBPS + AUDIO_KBPS) / (LIMIT_MB * 8 * 1024))
    seg = total / parts
    vkbps = int(min(MAX_VIDEO_KBPS, LIMIT_MB * 8 * 1024 / seg - AUDIO_KBPS))
    print(f"时长 {total:.0f} 秒，压缩成 {parts} 个文件，视频码率 {vkbps}k")
    outs = []
    for i in range(parts):
        name = src.stem + ("_上传版.mp4" if parts == 1 else f"_上传版_第{i + 1}段.mp4")
        dst = src.with_name(name)
        print(f"  正在压缩 {name} …")
        encode(src, dst, i * seg if parts > 1 else 0, seg if parts > 1 else 0, vkbps)
        size = dst.stat().st_size / 1024 / 1024
        if size > 29.5:  # 极少见：码率控制没压住，降一档重压
            encode(src, dst, i * seg if parts > 1 else 0, seg if parts > 1 else 0, int(vkbps * 0.8))
            size = dst.stat().st_size / 1024 / 1024
        outs.append((dst.name, size))
    print("完成：")
    for n, s in outs:
        print(f"  {n}  {s:.1f}MB")
    print("把这些文件上传给 Claude 即可（多段的按顺序传）。原片别删，最后在原片上出成片。")


if __name__ == "__main__":
    main()
