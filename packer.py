# -*- coding: utf-8 -*-
"""出片引擎：按方案剪切 → 推镜头 → 动态字幕（关键词变色弹跳/大字弹出/顶部标题）→ 音效 + BGM 混音。"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import settings as SET
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

    trims = plan.get("trim") or {}
    gone = {p["id"] for p in phrases if p["id"] in delete or p.get("filler")}

    def span(i):
        p = phrases[i]
        ds, de = trims.get(str(p["id"]), (0.0, 0.0))
        # 相邻句被删掉时，允许把入出点拖进它的范围（用户在时间线上手动微调）
        if i == 0:
            lo = 0.0
        else:
            q = phrases[i - 1]
            lo = (q["start"] if q["id"] in gone else q["end"]) if (ds or de) else q["end"]
        if i + 1 >= len(phrases):
            hi = duration
        else:
            q = phrases[i + 1]
            hi = (q["end"] if q["id"] in gone else q["start"]) if (ds or de) else q["start"]
        s = max(lo, p["start"] - PAD_IN + ds, 0.0)
        e = min(hi, p["end"] + PAD_OUT + de, duration)
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
def _atempo(sp):
    """atempo 单级只支持 0.5～2.0，这里够用；保留函数便于以后扩展。"""
    return f"atempo={sp:.3f}"


def render_segments(src, ranges, meta, tmp, log=print, progress=None, st=None, canvas=None):
    st = st or SET.normalize({})
    W, H = canvas or (meta["w"], meta["h"])
    fps = meta["fps"]
    sp = st["speed"]
    files, durs = [], []
    for k, r in enumerate(ranges, 1):
        d = r["end"] - r["start"]
        seg = tmp / f"seg{k:04d}.mp4"
        vf = SET.video_filters(meta, st, W, H, "[0:v]", "")
        if r["zoom"]:
            frames = max(1, int(meta["fpsf"] * ZOOM_IN_SEC))
            vf += (f",zoompan=z='min(1+{ZOOM_TO - 1:.3f}*on/{frames},{ZOOM_TO})'"
                   f":x='iw/2-iw/zoom/2':y='(ih-ih/zoom)*0.35':d=1:s={W}x{H}:fps={fps}")
        if sp != 1:
            vf += f",setpts=PTS/{sp:.3f}"
        vf += "[v]"
        fade = min(0.015, d / 4)
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{r['start']:.3f}", "-t", f"{d:.3f}", "-i", str(src),
               "-filter_complex", vf, "-map", "[v]"]
        if meta["has_audio"]:
            af = f"afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}"
            if st["audio"]["denoise"]:
                af = "afftdn=nf=-25," + af
            if sp != 1:
                af += "," + _atempo(sp)
            cmd += ["-map", "0:a:0", "-af", af,
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
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


def display_text(p, plan):
    """字幕上显示的文字：用户/AI 改过错字就用改后的。"""
    return ((plan.get("text") or {}).get(str(p["id"])) or p["text"])


def kw_time(p, kw, plan):
    """关键词出现的原片时间：原文里有就用逐字时间，改过字的按字数比例估计。"""
    if kw in p["text"]:
        return keyword_time(p, kw)
    t = display_text(p, plan)
    i = t.find(kw)
    return p["start"] + (p["end"] - p["start"]) * (max(0, i) / max(1, len(t)))


def build_events(ranges, durs, plan):
    """把方案换算到成片时间：字幕行、大字弹出、音效点（已考虑整体变速）。"""
    emph = {e["id"]: e for e in plan.get("emphasis", [])}
    sp = SET.of(plan)["speed"]
    subs, pops, sfx = [], [], []
    off = 0.0
    used = {}
    for r, real in zip(ranges, durs):
        for p in r["phrases"]:
            a = off + max(0.0, p["start"] - r["start"]) / sp
            b = min(off + real, off + (p["end"] - r["start"]) / sp + 0.05)
            if b <= a:
                continue
            e = emph.get(p["id"])
            kws = []
            if e:
                for kw in e["keywords"]:
                    kt = off + max(0.0, kw_time(p, kw, plan) - r["start"]) / sp
                    kws.append((kw, min(max(kt, a), b)))
            subs.append({"a": a, "b": b, "text": display_text(p, plan), "kws": kws,
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


def _text_width(t):
    """估算显示宽度（以一个汉字为 1）：英文数字约半个字宽。"""
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in t)


def wrap_cn(text, max_w, keep=()):
    """中文字幕自动折行（libass 只在空格处断行，中文长句会跑出画面）。
    折成宽度尽量相等的几行，不把 keep 里的重点词拆开；返回用 \n 分隔的文本。"""
    if _text_width(text) <= max_w:
        return text
    n = int(_text_width(text) // max_w) + 1
    protected = set()
    for kw in keep:
        i = text.find(kw)
        if i >= 0:
            protected.update(range(i + 1, i + len(kw)))
    lines, start = [], 0
    for k in range(1, n):
        goal = _text_width(text) * k / n
        acc, best = 0.0, None
        for i in range(start + 1, len(text)):
            acc = _text_width(text[:i])
            if i in protected:
                continue
            if best is None or abs(acc - goal) < abs(_text_width(text[:best]) - goal):
                best = i
            if acc > goal + 2:
                break
        if best is None or best <= start:
            continue
        cut = best + 1 if text[best:best + 1] == " " else best
        lines.append(text[start:best].strip())
        start = cut
    lines.append(text[start:].strip())
    return "\n".join(x for x in lines if x)


def write_ass(path, subs, pops, title, total, meta, font, sub=None):
    W, H = meta["w"], meta["h"]
    portrait = H >= W
    S = round(min(W, H) * 0.058)
    ol = max(2, round(S / 18))
    sub = sub or SET.DEFAULT["sub"]
    SS = round(S * sub["size"])
    mv_sub = round(H * (sub["pos"] / 100 if sub["pos"] else (0.17 if portrait else 0.07)))
    prim = SET.ass_color(sub["color"])
    if sub["style"] == "box":        # 半透明黑底框
        sub_style = f"{prim},&H00FFFFFF,&H60000000,&H60000000,1,0,0,0,100,100,1,0,3,{round(SS * 0.22)},0"
    elif sub["style"] == "shadow":   # 无描边，柔和投影
        sub_style = f"{prim},&H00FFFFFF,&H00000000,&H50000000,1,0,0,0,100,100,1,0,1,0,{max(2, round(SS / 10))}"
    else:                            # 黑描边（默认）
        sub_style = f"{prim},&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,1,0,1,{max(2, round(SS / 18))},{max(1, ol // 2)}"
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
Style: Sub,{font},{SS},{sub_style},2,{round(W * 0.06)},{round(W * 0.06)},{mv_sub},1
Style: Pop,{font},{round(S * 2.1)},&H0000D4FF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,{ol * 2},{ol},8,{round(W * 0.05)},{round(W * 0.05)},{mv_pop},1
Style: Title,{font},{round(S * 1.1)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,1,0,0,0,100,100,2,0,3,{round(S * 0.35)},0,8,{round(W * 0.08)},{round(W * 0.08)},{mv_title},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(total)},Title,,0,0,0,,{{\\fad(300,300)}}{_esc(title)}")
    max_w = W * 0.88 / max(1, SS)          # 一行最多放多少个汉字宽
    for s in subs:
        text = _esc(wrap_cn(_clean(s["text"]), max_w, [_clean(k) for k, _ in s["kws"]]))
        for kw, kt in s["kws"]:
            ckw = _esc(_clean(kw))
            if not ckw or ckw not in text:
                continue
            dt = int(max(0.0, kt - s["a"]) * 1000)
            col = ASS_COLOR.get(s["color"], ASS_COLOR["yellow"])
            tag = (f"{{\\c{col}\\fscx100\\fscy100\\t({dt},{dt + 110},\\fscx128\\fscy128)"
                   f"\\t({dt + 110},{dt + 240},\\fscx112\\fscy112)}}")
            text = text.replace(ckw, tag + ckw + "{\\r}", 1)
        text = text.replace("\n", "\\N")
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
def final_mix(body, workdir, out_mp4, sfx, bgm_kind, total, meta, bgm_vol=0.10, overlays=(), st=None):
    st = st or SET.normalize({})
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
    vf = f"{vin}ass=subs.ass"
    if st["fade"]["in"]:
        vf += ",fade=t=in:st=0:d=0.6"
    if st["fade"]["out"]:
        vf += f",fade=t=out:st={max(0.0, total - 0.8):.3f}:d=0.8"
    target = SET.RESOLUTIONS[st["export"]["res"]]
    if target and min(meta["w"], meta["h"]) > target:   # 只缩小不放大
        vf += (f",scale={target}:-2" if meta["w"] <= meta["h"] else f",scale=-2:{target}") + ",setsar=1"
    parts.append(vf + "[v]")
    if labels:
        amix = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first"
        if st["audio"]["loudnorm"]:      # 统一到短视频平台常用响度 -14 LUFS
            amix += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        if st["fade"]["in"]:
            amix += ",afade=t=in:d=0.4"
        if st["fade"]["out"]:
            amix += f",afade=t=out:st={max(0.0, total - 0.8):.3f}:d=0.8"
        parts.append(amix + ",alimiter=limit=0.95[aout]")
        amap = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        amap = []
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"] + amap + [
        "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", str(SET.QUALITY_CRF[st["export"]["quality"]]),
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
    sp = SET.of(plan)["speed"]
    out, off, done = [], 0.0, set()
    for r, real in zip(ranges, durs):
        for p in r["phrases"]:
            b = want.get(p["id"])
            if b and not r.get("hook") and p["id"] not in done:
                a = off + max(0.0, p["start"] - r["start"]) / sp
                e = off + min(real, (p["end"] - r["start"]) / sp)
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
    src_meta = probe(src)
    st = SET.of(plan)
    W, H = SET.canvas_size(src_meta, st)
    meta = dict(src_meta, w=W, h=H)          # 之后的字幕/配图都按成片画布算
    ranges = build_timeline(data, plan)
    if not ranges:
        raise RuntimeError("方案里没有要保留的内容。")
    tmp = Path(tempfile.mkdtemp(prefix="koubo_", dir=str(out_dir)))
    try:
        extra = []
        if st["speed"] != 1:
            extra.append(f"{st['speed']}倍速")
        if (W, H) != (src_meta["w"], src_meta["h"]):
            extra.append(f"画布 {W}×{H}")
        log(f"剪切 {len(ranges)} 段…" + ("（" + "，".join(extra) + "）" if extra else ""))
        body, durs = render_segments(src, ranges, src_meta, tmp, log,
                                     (lambda x: progress(0.75 * x)) if progress else None, st, (W, H))
        subs, pops, sfx, total = build_events(ranges, durs, plan)
        write_ass(tmp / "subs.ass", subs, pops, plan.get("title", ""), total, meta, font, st["sub"])
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
        final_mix(body, tmp, out_mp4, sfx, plan.get("bgm", "none"), total, meta, bgm_vol, overlays, st)
        write_srt(out_dir / "字幕.srt", subs)
        shutil.copy(tmp / "subs.ass", out_dir / "包装字幕.ass")
        (out_dir / "方案.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        if progress:
            progress(1.0)
        log(f"完成：原片 {src_meta['duration']:.0f} 秒 → 成片 {total:.0f} 秒")
        return {"video": str(out_mp4), "duration": total, "segments": len(ranges), "sfx": len(sfx),
                "broll": len(overlays)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 封面
def _cover_lines(text, per_line):
    text = text.strip()
    if "\n" in text:
        return [x.strip() for x in text.split("\n") if x.strip()][:3]
    if len(text) <= per_line:
        return [text]
    cut = (len(text) + 1) // 2
    return [text[:cut], text[cut:]]


def make_cover(src, t, text, plan, out_jpg, font="Microsoft YaHei"):
    """从原片第 t 秒取一帧（套用画布和调色），压上大标题，存成 jpg。"""
    src_meta = probe(src)
    st = SET.of(plan)
    W, H = SET.canvas_size(src_meta, st)
    S = round(min(W, H) * 0.058)
    portrait = H >= W
    lines = _cover_lines(text or plan.get("title") or "", 7 if portrait else 12)
    fs = round(S * (2.5 if portrait else 2.2))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: C,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,2,0,1,{max(4, fs // 11)},{max(3, fs // 16)},8,{round(W * 0.05)},{round(W * 0.05)},{round(H * (0.16 if portrait else 0.1))},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for i, ln in enumerate(lines):   # 一行白一行黄，最常见的口播封面配色
        col = "&H00FFFFFF&" if i % 2 == 0 else "&H0000D4FF&"
        body.append(f"{{\\c{col}}}{_esc(ln)}")
    joined = "\\N".join(body)
    ev = f"Dialogue: 0,0:00:00.00,0:00:05.00,C,,0,0,0,,{joined}\n" if lines else ""
    tmp = Path(tempfile.mkdtemp(prefix="koubo_cover_", dir=str(Path(out_jpg).parent)))
    try:
        (tmp / "cover.ass").write_text(head + ev, encoding="utf-8-sig")
        vf = SET.video_filters(src_meta, st, W, H, "[0:v]", "") + ",ass=cover.ass[v]"
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
                        "-i", str(Path(src).resolve()), "-frames:v", "1", "-filter_complex", vf, "-map", "[v]",
                        "-q:v", "2", str(Path(out_jpg).resolve())], check=True, cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out_jpg)
