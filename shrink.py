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


HD_LIMIT_GB = 1.9      # GitHub Release 单个附件上限 2GB，留点余量


def hd(src):
    """高清上传版：短边 1080、画质接近原片，用来上传到 GitHub / 云存储，云端可以直接出最终成片。"""
    dst = src.with_name(src.stem + "_高清上传版.mp4")
    for crf in (21, 24, 27):
        print(f"正在压缩高清上传版（画质 {crf}，数字越小越清晰）…")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(src),
                        "-vf", "scale='if(gt(iw,ih),-2,min(1080,iw))':'if(gt(iw,ih),min(1080,ih),-2)'",
                        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)], check=True)
        gb = dst.stat().st_size / 1024 ** 3
        if gb <= HD_LIMIT_GB:
            break
        print(f"  {gb:.2f}GB 还是太大，再压一档…")
    mb = dst.stat().st_size / 1024 ** 2
    print(f"完成：{dst.name}  {mb:.0f}MB（原片 {src.stat().st_size / 1024 ** 2:.0f}MB）")
    print("上传方法：GitHub 打开 koubo-studio 仓库 → Releases → Draft a new release → 把这个文件拖进去 → Save draft，")
    print("然后在 Claude 里说「视频传好了」。")


def main():
    if "--hd" in sys.argv:
        sys.argv.remove("--hd")
        return hd(video_arg())
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
