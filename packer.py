# -*- coding: utf-8 -*-
"""出片引擎：按方案剪切 → 推镜头 → 动态字幕（关键词变色弹跳/大字弹出/顶部标题）→ 音效 + BGM 混音。"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from asr import keyword_time
from common import find_tool, fmt_srt

APP = Path(__file__).resolve().parent
SFX_DIR = APP / "assets" / "sfx"
BGM_DIR = APP / "assets" / "bgm"

PAD_IN, PAD_OUT, MERGE = 0.08, 0.12, 0.15
ZOOM_TO, ZOOM_IN_SEC = 1.10, 0.45

SFX_FILES = {
    "whoosh": ["whoosh-fast.mp3", "air-woosh-quick.mp3", "sweep-fast-small.mp3"],
    "hit": ["bass-hit-short.mp3", "impact-zoom-quick.mp3", "hit-fast-exciting.mp3"],
    "ding": ["ui-confirm-tone.mp3", "chime-crystal.mp3", "ui-success-soft.mp3"],
    "pop": ["ui-message-pop.mp3", "pop-electric.mp3", "ui-popup-dry.mp3"],
    "type": ["typewriter-digital.mp3", "typewriter-hit-soft.mp3"],
    "page": ["paper-page-turn.mp3", "paper-slide.mp3"],
    "sparkle": ["sparkle-touch.mp3", "shimmer-sparkle-sweep.mp3"],
    "tick": ["clock-tick-single.mp3", "clock-knob-spin.mp3"],
    "hook": ["impact-movie-epic.mp3"],
}
SFX_VOL = {"whoosh": 0.45, "hit": 0.55, "ding": 0.45, "pop": 0.5, "type": 0.5,
           "page": 0.6, "sparkle": 0.45, "tick": 0.5, "hook": 0.45}
SFX_LEAD = {"whoosh": 0.12, "hook": 0.0}
BGM_FILES = {"upbeat": "house-vibez.mp3", "chill": "cat-walk.mp3", "hiphop": "g-eazy-nba-type.mp3"}
ASS_COLOR = {"yellow": "&H0000D4FF&", "red": "&H00303BFF&", "blue": "&H00FFA63E&"}


def _ffmpeg():
    return find_tool("ffmpeg")


def _ffprobe():
    return find_tool("ffprobe")


def probe(path):
    out = subprocess.run([_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8").stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(v.get("tags", {}).get("rotate", rot))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = v.get("avg_frame_rate", "30/1")
    try:
        n, d = fps.split("/")
        fpsf = float(n) / float(d)
    except (ValueError, ZeroDivisionError):
        fpsf = 30.0
    if not 10 <= fpsf <= 121:
        fps, fpsf = "30", 30.0
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return {"w": w, "h": h, "fps": fps, "fpsf": fpsf, "has_audio": has_audio,
            "duration": float(info["format"]["duration"])}


def seg_duration(path):
    out = subprocess.run([_ffprobe(), "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout
    return float(out.strip())


# ---------------------------------------------------------------- 时间线
def build_timeline(data, plan):
    phrases = data["phrases"]
    duration = float(data["duration"])
    delete = set(plan.get("delete", []))
    emph = {e["id"]: e for e in plan.get("emphasis", [])}
    keep = [i for i, p in enumerate(phrases) if p["id"] not in delete and not p.get("filler")]

    def span(i):
        p = phrases[i]
        lo = phrases[i - 1]["end"] if i > 0 else 0.0
        hi = phrases[i + 1]["start"] if i + 1 < len(phrases) else duration
        s = max(lo, p["start"] - PAD_IN, 0.0)
        e = min(hi, p["end"] + PAD_OUT, duration)
        return s, e

    ranges = []
    hook = plan.get("hook")
    if hook is not None:
        hi = next((i for i, p in enumerate(phrases) if p["id"] == hook), None)
        if hi is not None and keep and keep[0] != hi:
            s, e = span(hi)
            ranges.append({"start": s, "end": e, "phrases": [phrases[hi]], "zoom": True, "hook": True, "last": -9})
    for i in keep:
        s, e = span(i)
        if e <= s:
            continue
        p = phrases[i]
        z = bool(emph.get(p["id"], {}).get("zoom"))
        last = ranges[-1] if ranges else None
        if (last and not last.get("hook") and last["last"] == i - 1 and not z and not last["zoom"]
                and s - last["end"] < MERGE):
            last["end"] = e
            last["phrases"].append(p)
            last["last"] = i
        else:
            ranges.append({"start": s, "end": e, "phrases": [p], "zoom": z, "hook": False, "last": i})
    return ranges


# ---------------------------------------------------------------- 切片
def render_segments(src, ranges, meta, tmp, log=print, progress=None):
    W, H, fps = meta["w"], meta["h"], meta["fps"]
    files, durs = [], []
    for k, r in enumerate(ranges, 1):
        d = r["end"] - r["start"]
        seg = tmp / f"seg{k:04d}.mp4"
        vf = f"scale={W}:{H},setsar=1"
        if r["zoom"]:
            frames = max(1, int(meta["fpsf"] * ZOOM_IN_SEC))
            vf += (f",zoompan=z='min(1+{ZOOM_TO - 1:.3f}*on/{frames},{ZOOM_TO})'"
                   f":x='iw/2-iw/zoom/2':y='(ih-ih/zoom)*0.35':d=1:s={W}x{H}:fps={fps}")
        fade = min(0.015, d / 4)
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{r['start']:.3f}", "-i", str(src), "-t", f"{d:.3f}",
               "-map", "0:v:0"]
        if meta["has_audio"]:
            cmd += ["-map", "0:a:0", "-af",
                    f"afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", fps, str(seg)]
        subprocess.run(cmd, check=True)
        files.append(seg)
        durs.append(seg_duration(seg))
        if progress:
            progress(k / len(ranges))
    lst = tmp / "list.txt"
    lst.write_text("\n".join("file '" + f.as_posix().replace("'", "'\\''") + "'" for f in files) + "\n",
                   encoding="utf-8")
    body = tmp / "body.mp4"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(body)], check=True)
    return body, durs


# ---------------------------------------------------------------- 字幕 / 包装
def _clean(text):
    t = text.strip()
    for ch in "，。！？；、,.!?;：:":
        t = t.replace(ch, " ")
    return " ".join(t.split())


def _ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(t):
    return t.replace("{", "（").replace("}", "）").replace("\\", "/")


def build_events(ranges, durs, plan):
    """把方案换算到成片时间：字幕行、大字弹出、音效点。"""
    emph = {e["id"]: e for e in plan.get("emphasis", [])}
    subs, pops, sfx = [], [], []
    off = 0.0
    used = {}
    for r, real in zip(ranges, durs):
        for p in r["phrases"]:
            a = off + max(0.0, p["start"] - r["start"])
            b = min(off + real, off + p["end"] - r["start"] + 0.05)
            if b <= a:
                continue
            e = emph.get(p["id"])
            kws = []
            if e:
                for kw in e["keywords"]:
                    kt = off + max(0.0, keyword_time(p, kw) - r["start"])
                    kws.append((kw, min(max(kt, a), b)))
            subs.append({"a": a, "b": b, "text": p["text"], "kws": kws,
                         "color": (e or {}).get("color", "yellow")})
            if e and e["big"] and kws:
                kw, kt = kws[0]
                pops.append({"a": kt, "b": max(b + 0.25, kt + 1.0), "text": kw, "color": e["color"],
                             "effect": e.get("effect", "pop")})
            if e and e["sfx"] != "none":
                t = (kws[0][1] if kws else a) - SFX_LEAD.get(e["sfx"], 0.03)
                sfx.append({"t": max(0.0, t), "kind": e["sfx"]})
        if r.get("hook"):
            sfx.append({"t": 0.0, "kind": "hook"})
            sfx.append({"t": max(0.0, off + real - 0.15), "kind": "whoosh"})
        off += real
    # 同类音效轮换不同文件，避免听觉疲劳
    for s in sorted(sfx, key=lambda x: x["t"]):
        files = SFX_FILES[s["kind"]]
        s["file"] = files[used.get(s["kind"], 0) % len(files)]
        used[s["kind"]] = used.get(s["kind"], 0) + 1
    return subs, pops, sfx, off


def write_ass(path, subs, pops, title, total, meta, font):
    W, H = meta["w"], meta["h"]
    portrait = H >= W
    S = round(min(W, H) * 0.058)
    ol = max(2, round(S / 18))
    mv_sub = round(H * (0.17 if portrait else 0.07))
    mv_pop = round(H * (0.27 if portrait else 0.16))
    mv_title = round(H * (0.07 if portrait else 0.05))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{S},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,1,0,1,{ol},{max(1, ol // 2)},2,{round(W * 0.06)},{round(W * 0.06)},{mv_sub},1
Style: Pop,{font},{round(S * 2.1)},&H0000D4FF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,{ol * 2},{ol},8,{round(W * 0.05)},{round(W * 0.05)},{mv_pop},1
Style: Title,{font},{round(S * 1.1)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,1,0,0,0,100,100,2,0,3,{round(S * 0.35)},0,8,{round(W * 0.08)},{round(W * 0.08)},{mv_title},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(total)},Title,,0,0,0,,{{\\fad(300,300)}}{_esc(title)}")
    for s in subs:
        text = _esc(_clean(s["text"]))
        for kw, kt in s["kws"]:
            ckw = _esc(_clean(kw))
            if not ckw or ckw not in text:
                continue
            dt = int(max(0.0, kt - s["a"]) * 1000)
            col = ASS_COLOR.get(s["color"], ASS_COLOR["yellow"])
            tag = (f"{{\\c{col}\\fscx100\\fscy100\\t({dt},{dt + 110},\\fscx128\\fscy128)"
                   f"\\t({dt + 110},{dt + 240},\\fscx112\\fscy112)}}")
            text = text.replace(ckw, tag + ckw + "{\\r}", 1)
        lines.append(f"Dialogue: 1,{_ass_time(s['a'])},{_ass_time(s['b'])},Sub,,0,0,0,,{{\\fad(60,60)}}{text}")
    for p in pops:
        fx = p.get("effect", "pop")
        if fx == "count" and _NUM_RE.search(p["text"]):
            lines += _count_lines(p, W, H, mv_pop, S)
        elif fx == "card":
            lines += _card_lines(p, W, H, mv_pop, S, font)
        else:
            col = ASS_COLOR.get(p["color"], ASS_COLOR["yellow"])
            lines.append(f"Dialogue: 3,{_ass_time(p['a'])},{_ass_time(p['b'])},Pop,,0,0,0,,"
                         f"{{\\c{col}\\fad(60,180)\\fscx35\\fscy35\\t(0,160,\\fscx118\\fscy118)"
                         f"\\t(160,300,\\fscx100\\fscy100)}}{_esc(_clean(p['text']))}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")


# ---------------------------------------------------------------- 高级包装：数字滚动 / 3D 卡片
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
COUNT_SEC, COUNT_STEPS = 0.7, 16
CARD_BG = {"yellow": "&H0000C8FF&", "red": "&H002A2AE8&", "blue": "&H00E8902E&"}


def _count_lines(p, W, H, mv, S):
    """数字从 0 滚到目标值（先快后慢），滚完停在原文上并弹一下。"""
    text = _clean(p["text"])
    m = _NUM_RE.search(text)
    pre, num, post = text[:m.start()], m.group(0), text[m.end():]
    target = float(num)
    dec = len(num.split(".")[1]) if "." in num else 0
    col = ASS_COLOR.get(p["color"], ASS_COLOR["yellow"])
    a, b = p["a"], p["b"]
    roll = min(COUNT_SEC, max(0.3, (b - a) * 0.5))
    pos = f"\\an8\\pos({W // 2},{mv})"
    out, step = [], roll / COUNT_STEPS
    for i in range(COUNT_STEPS):
        k = 1 - (1 - i / COUNT_STEPS) ** 3          # ease-out
        v = target * k
        shown = f"{v:.{dec}f}" if dec else str(int(v))
        t0, t1 = a + i * step, a + (i + 1) * step
        fade = "\\fad(60,0)" if i == 0 else ""
        out.append(f"Dialogue: 3,{_ass_time(t0)},{_ass_time(t1)},Pop,,0,0,0,,"
                   f"{{{pos}{fade}\\c{col}\\blur{0.6 if i < COUNT_STEPS - 3 else 0}}}"
                   f"{_esc(pre)}{{\\c{col}}}{shown}{_esc(post)}")
    out.append(f"Dialogue: 3,{_ass_time(a + roll)},{_ass_time(b)},Pop,,0,0,0,,"
               f"{{{pos}\\c{col}\\fad(0,180)\\t(0,110,\\fscx122\\fscy122)\\t(110,260,\\fscx100\\fscy100)}}"
               f"{_esc(text)}")
    return out


def _round_rect(w, h, r):
    r = min(r, h / 2, w / 2)
    k = r * 0.45
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


def _card_lines(p, W, H, mv, S, font):
    """3D 翻转卡片：圆角色块 + 白字，绕 Y 轴从侧面翻进来，停留时保持轻微透视倾斜。"""
    text = _esc(_clean(p["text"]))
    fs = round(S * 1.7)
    tw = sum(fs if ord(c) > 0x2E80 else fs * 0.58 for c in text)
    cw, ch = round(tw + fs * 1.3), round(fs * 1.75)
    cx, cy = W // 2, mv + round(ch * 0.6 + S * 0.35)
    bg = CARD_BG.get(p["color"], CARD_BG["yellow"])
    fg, ol = ("&H00181818&", "&H00FFFFFF&") if p["color"] == "yellow" else ("&H00FFFFFF&", "&H00202020&")
    a, b = _ass_time(p["a"]), _ass_time(p["b"])
    org = f"\\org({cx},{cy})"
    anim = (f"\\fry-80\\frx10\\fscx80\\fscy80\\t(0,240,0.7,\\fry6\\frx2\\fscx106\\fscy106)"
            f"\\t(240,400,\\fry-10\\frx4\\fscx100\\fscy100)\\fad(40,200)")
    shadow = (f"Dialogue: 3,{a},{b},Pop,,0,0,0,,{{\\an5\\pos({cx + fs // 7},{cy + fs // 6}){org}{anim}"
              f"\\bord0\\shad0\\blur8\\1c&H000000&\\1a&H70&\\p1}}{_round_rect(cw, ch, ch * 0.28)}{{\\p0}}")
    card = (f"Dialogue: 4,{a},{b},Pop,,0,0,0,,{{\\an5\\pos({cx},{cy}){org}{anim}"
            f"\\bord0\\shad0\\1c{bg}\\p1}}{_round_rect(cw, ch, ch * 0.28)}{{\\p0}}")
    word = (f"Dialogue: 5,{a},{b},Pop,,0,0,0,,{{\\an5\\pos({cx},{cy}){org}{anim}"
            f"\\fs{fs}\\1c{fg}\\bord{max(2, fs // 28)}\\3c{ol}\\shad0}}{text}")
    return [shadow, card, word]


def write_srt(path, subs):
    blocks = [f"{i}\n{fmt_srt(s['a'])} --> {fmt_srt(s['b'])}\n{_clean(s['text'])}\n"
              for i, s in enumerate(subs, 1)]
    Path(path).write_text("\n".join(blocks), encoding="utf-8-sig")


# ---------------------------------------------------------------- 混音 + 烧字幕
def final_mix(body, workdir, out_mp4, sfx, bgm_kind, total, meta, bgm_vol=0.10, overlays=()):
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(body)]
    parts, labels = [], []
    if meta["has_audio"]:
        parts.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0]")
        labels.append("[a0]")
    idx = 1
    for k, s in enumerate(sfx):
        f = SFX_DIR / s["file"]
        if not f.is_file():
            continue
        cmd += ["-i", str(f)]
        ms = int(s["t"] * 1000)
        parts.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume={SFX_VOL[s['kind']]},adelay={ms}|{ms}[s{k}]")
        labels.append(f"[s{k}]")
        idx += 1
    bgm = BGM_FILES.get(bgm_kind)
    if bgm and (BGM_DIR / bgm).is_file():
        cmd += ["-stream_loop", "-1", "-i", str(BGM_DIR / bgm)]
        fo = max(0.0, total - 2.0)
        parts.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={bgm_vol},"
                     f"atrim=0:{total:.3f},afade=t=in:d=1,afade=t=out:st={fo:.3f}:d=2[bg]")
        labels.append("[bg]")
        idx += 1
    vin = "[0:v]"
    for k, o in enumerate(overlays):
        cmd += ["-i", str(o["clip"])]
        d, f = o["b"] - o["a"], BROLL_FADE
        parts.append(f"[{idx}:v]format=yuva420p,fade=t=in:st=0:d={f}:alpha=1,"
                     f"fade=t=out:st={max(0.0, d - f):.3f}:d={f}:alpha=1,setpts=PTS-STARTPTS+{o['a']:.3f}/TB[b{k}]")
        parts.append(f"{vin}[b{k}]overlay={o['x']}:{o['y']}:eof_action=pass[v{k}]")
        vin = f"[v{k}]"
        idx += 1
    parts.append(f"{vin}ass=subs.ass[v]")
    if labels:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,"
                     f"alimiter=limit=0.95[aout]")
        amap = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        amap = []
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"] + amap + [
        "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True, cwd=str(workdir))


# ---------------------------------------------------------------- 配图
BROLL_MIN, BROLL_FADE, BROLL_BORDER = 2.2, 0.2, 6


def _even(v):
    return int(round(v / 2)) * 2


def broll_box(layout, W, H):
    """配图在画面里的位置和大小 (x, y, w, h)，含白边。"""
    portrait = H >= W
    if layout == "full":
        return 0, 0, W, H
    if layout == "corner":
        w = _even((W if portrait else H) * 0.36)
        return _even(W - w - W * 0.05), _even(H * (0.14 if portrait else 0.08)), w, w
    if portrait:
        # 竖屏口播脸在中上部：图片放在胸前、字幕上方，不挡脸
        w = _even(W * 0.88)
        h = _even(w * 0.75)
        S = min(W, H) * 0.058
        bottom = H - H * 0.17 - S * 1.7
        return _even((W - w) / 2), _even(bottom - h), w, h
    h = _even(H * 0.6)
    w = _even(h * 4 / 3)
    return _even(W - w - W * 0.04), _even((H - h) / 2 - H * 0.06), w, h


def broll_events(ranges, durs, plan, total):
    """配图出现的成片时间：从这句开始，至少停留 BROLL_MIN 秒。钩子复制段不放配图。"""
    want = {b["id"]: b for b in plan.get("broll", []) if b.get("image")}
    out, off, done = [], 0.0, set()
    for r, real in zip(ranges, durs):
        for p in r["phrases"]:
            b = want.get(p["id"])
            if b and not r.get("hook") and p["id"] not in done:
                a = off + max(0.0, p["start"] - r["start"])
                e = off + min(real, p["end"] - r["start"])
                out.append({"a": a, "b": min(total, max(e, a + BROLL_MIN)), "item": b})
                done.add(p["id"])
        off += real
    out.sort(key=lambda x: x["a"])
    for x, y in zip(out, out[1:]):          # 不重叠
        x["b"] = min(x["b"], y["a"])
    return [x for x in out if x["b"] - x["a"] >= 0.8]


def make_broll_clip(img, layout, dur, meta, out):
    """把图片做成一段带缓慢推拉（Ken Burns）的视频，画中画/小窗加白边。"""
    x, y, w, h = broll_box(layout, meta["w"], meta["h"])
    bd = 0 if layout == "full" else BROLL_BORDER
    iw, ih = w - 2 * bd, h - 2 * bd
    bw, bh = _even(iw * 1.12), _even(ih * 1.12)
    vf = (f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
          f"crop={iw}:{ih}:x='(iw-ow)*t/{dur:.3f}':y='(ih-oh)/2',setsar=1")
    if bd:
        vf += f",pad={w}:{h}:{bd}:{bd}:white"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-t", f"{dur:.3f}",
                    "-i", str(img), "-vf", vf, "-r", meta["fps"], "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "18", "-pix_fmt", "yuv420p", str(out)], check=True)
    return {"x": x, "y": y}


def render(src, data, plan, out_dir, font="Microsoft YaHei", bgm_vol=0.10, log=print, progress=None,
           broll_dir=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = probe(src)
    ranges = build_timeline(data, plan)
    if not ranges:
        raise RuntimeError("方案里没有要保留的内容。")
    tmp = Path(tempfile.mkdtemp(prefix="koubo_", dir=str(out_dir)))
    try:
        log(f"剪切 {len(ranges)} 段…")
        body, durs = render_segments(src, ranges, meta, tmp, log,
                                     (lambda x: progress(0.75 * x)) if progress else None)
        subs, pops, sfx, total = build_events(ranges, durs, plan)
        write_ass(tmp / "subs.ass", subs, pops, plan.get("title", ""), total, meta, font)
        overlays = []
        if broll_dir:
            for k, ev in enumerate(broll_events(ranges, durs, plan, total)):
                img = Path(broll_dir) / ev["item"]["image"]
                if not img.is_file():
                    log(f"配图文件不见了，跳过：{img.name}")
                    continue
                clip = tmp / f"broll{k:03d}.mp4"
                pos = make_broll_clip(img, ev["item"]["layout"], ev["b"] - ev["a"], meta, clip)
                overlays.append(dict(pos, clip=clip, a=ev["a"], b=ev["b"]))
        log(f"加配图 {len(overlays)} 张、字幕、音效 {len(sfx)} 个、BGM…")
        out_mp4 = out_dir / "成片.mp4"
        final_mix(body, tmp, out_mp4, sfx, plan.get("bgm", "none"), total, meta, bgm_vol, overlays)
        write_srt(out_dir / "字幕.srt", subs)
        shutil.copy(tmp / "subs.ass", out_dir / "包装字幕.ass")
        (out_dir / "方案.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        if progress:
            progress(1.0)
        log(f"完成：原片 {meta['duration']:.0f} 秒 → 成片 {total:.0f} 秒")
        return {"video": str(out_mp4), "duration": total, "segments": len(ranges), "sfx": len(sfx),
                "broll": len(overlays)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
