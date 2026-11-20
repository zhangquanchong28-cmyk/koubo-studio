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
import styles
import typo
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
# 音效基准音量（再乘全片设置里的音效倍数）。规范要求音效约为人声 10%～15%，比早期版本整体调轻
SFX_VOL = {"whoosh": 0.27, "hit": 0.33, "ding": 0.27, "pop": 0.3, "type": 0.3,
           "page": 0.36, "sparkle": 0.27, "tick": 0.3, "hook": 0.3}
SFX_LEAD = {"whoosh": 0.12, "hook": 0.0}
BGM_FILES = {"upbeat": "house-vibez.mp3", "chill": "cat-walk.mp3", "hiphop": "g-eazy-nba-type.mp3"}
# 高亮色（规范色值）：金 #FFB800、正红 #FF2B2B、深蓝 #2E7BFF、橙 #FF7A00、青绿 #00B4A0（ASS 为 BGR）
ASS_COLOR = {"yellow": "&H0000B8FF&", "red": "&H002B2BFF&", "blue": "&H00FF7B2E&",
             "orange": "&H00007AFF&", "green": "&H00A0B400&"}

FONTS_DIR = APP / "fonts"          # 内置思源黑体（SIL OFL，可商用）
BUILTIN_FONT = "Source Han Sans CN"


font_roles = typo.font_roles


def ass_filter(workdir, name):
    """ass 滤镜，带上内置字体目录（用相对路径，避开 Windows 盘符冒号的转义问题）。"""
    if FONTS_DIR.is_dir():
        try:
            rel = os.path.relpath(FONTS_DIR, workdir).replace("\\", "/")
            if ":" not in rel and "'" not in rel:
                return f"ass={name}:fontsdir={rel}"
        except ValueError:      # 不在同一个盘：把字体拷进工作目录
            pass
        local = Path(workdir) / "fonts"
        if not local.exists():
            shutil.copytree(FONTS_DIR, local)
        return f"ass={name}:fontsdir=fonts"
    return f"ass={name}"


def _ffmpeg():
    return find_tool("ffmpeg")


def _ffprobe():
    return find_tool("ffprobe")


def probe(path):
    out = subprocess.run([_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8").stdout
    info = json.loads(out)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    v = next((s for s in info["streams"] if s["codec_type"] == "video"
              and s.get("disposition", {}).get("attached_pic") != 1), None)
    if v is None:     # 纯音频（录音 mp3/m4a/wav）：只能做纯文字视频
        return {"w": 1080, "h": 1920, "fps": "30", "fpsf": 30.0, "has_audio": has_audio, "has_video": False,
                "duration": float(info["format"]["duration"])}
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
    return {"w": w, "h": h, "fps": fps, "fpsf": fpsf, "has_audio": has_audio, "has_video": True,
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
    pause = SET.of(plan)["pause"]

    def gap_keep(i):
        """第 i 句和第 i+1 句都保留时，两句之间保留多少停顿（句末多留一点，像换段落）。"""
        a, b = phrases[i], phrases[i + 1]
        g = max(0.0, b["start"] - a["end"])
        want = pause + (0.15 if a["text"].strip()[-1:] in "。？！?!" else 0.0)
        return min(g, want)

    def span(i):
        p = phrases[i]
        ds, de = trims.get(str(p["id"]), (0.0, 0.0))
        prev_kept = i > 0 and phrases[i - 1]["id"] not in gone
        next_kept = i + 1 < len(phrases) and phrases[i + 1]["id"] not in gone
        pad_in = gap_keep(i - 1) * 0.4 if prev_kept else PAD_IN
        pad_out = gap_keep(i) * 0.6 if next_kept else PAD_OUT
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
        s = max(lo, p["start"] - pad_in + ds, 0.0)
        e = min(hi, p["end"] + pad_out + de, duration)
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


def make_background(out, W, H, dur, st, fps="30"):
    """纯文字视频背景：深色=风格配色的缓慢流动渐变；浅色=米白纸张（细颗粒+暗角）。"""
    if st["bg"] == "light":
        src = (f"color=c=0xF2EDE4:s={W}x{H}:d={dur:.3f}:r={fps},noise=alls=5:allf=t,"
               f"vignette=angle=0.55,format=yuv420p")
    else:
        c0, c1, c2 = styles.bg_colors(st.get("style"))
        src = (f"gradients=s={W}x{H}:c0={c0}:c1={c1}:c2={c2}:nb_colors=3:speed=0.004:type=radial:d={dur:.3f}:r={fps},"
               f"vignette=angle=0.5,format=yuv420p")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)], check=True)


def render_text_body(src, ranges, meta, tmp, st, W, H, progress=None):
    """纯文字视频：按方案剪出人声，再配上自动生成的背景。"""
    sp = st["speed"]
    files, durs = [], []
    for k, r in enumerate(ranges, 1):
        d = r["end"] - r["start"]
        seg = tmp / f"aseg{k:04d}.m4a"
        fade = min(0.015, d / 4)
        af = f"afade=t=in:d={fade:.3f},afade=t=out:st={d - fade:.3f}:d={fade:.3f}"
        if st["audio"]["denoise"]:
            af = "afftdn=nf=-25," + af
        if sp != 1:
            af += "," + _atempo(sp)
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{r['start']:.3f}",
                        "-t", f"{d:.3f}", "-i", str(src), "-vn", "-map", "0:a:0", "-af", af,
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(seg)], check=True)
        files.append(seg)
        durs.append(seg_duration(seg))
        if progress:
            progress(k / len(ranges))
    lst = tmp / "alist.txt"
    lst.write_text("\n".join("file '" + f.as_posix().replace("'", "'\\''") + "'" for f in files) + "\n",
                   encoding="utf-8")
    voice = tmp / "voice.m4a"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(voice)], check=True)
    total = sum(durs)
    bg = tmp / "bg.mp4"
    make_background(bg, W, H, total + 0.5, st, meta.get("fps", "30"))
    body = tmp / "body.mp4"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(bg), "-i", str(voice),
                    "-map", "0:v", "-map", "1:a", "-c", "copy", "-t", f"{total:.3f}", str(body)], check=True)
    return body, durs


# ---------------------------------------------------------------- 字幕 / 包装
def _clean(text):
    t = re.sub(r"(?<=\d)\.(?=\d)", "\u2024", str(text).strip())   # 先保护小数点（0.25% 不能变成 0 25%）
    for ch in "，。！？；、,.!?;：:":
        t = t.replace(ch, " ")
    return " ".join(t.split()).replace("\u2024", ".")


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


SUB_LEAD = 0.05     # 字幕比人声早一点出现，观感上更同步
_BREAK_AFTER = "，。！？；、,.!?;：:"


_JIEBA = None


def _word_bounds(text):
    """可以断开的位置（词和词之间）。用 jieba 分词；没装就只能逐字。"""
    global _JIEBA
    if _JIEBA is None:
        try:
            import logging
            import jieba
            jieba.setLogLevel(logging.WARNING)
            _JIEBA = jieba
        except ImportError:
            _JIEBA = False
    if not _JIEBA:
        return list(range(len(text)))
    out, pos = [0], 0
    for w in _JIEBA.lcut(text):
        pos += len(w)
        out.append(pos)
    return [b for b in out if b < len(text)]


def split_phrase(p, plan, max_chars, keep=()):
    """把一句拆成每条不超过 max_chars 个字的字幕（用逐字时间），优先在标点处断，不拆开重点词。
    用 jieba 分词只在词和词之间断；尽量平均分成几条。返回 [(开始, 结束, 文字)]（原片时间）。"""
    text = display_text(p, plan)
    words = p.get("words") or []
    if _text_width(_clean(text)) <= max_chars:
        return [(p["start"], p["end"], text)]
    full = text
    # 每个字的开始时间：原文用逐字时间；改字后字数不变也能沿用；否则按字数比例估
    char_t = []
    if words:
        for ws, we, wt in words:
            for j in range(len(wt)):
                char_t.append(ws + (we - ws) * j / max(1, len(wt)))
    if len(char_t) != len(full):
        n = max(1, len(full))
        char_t = [p["start"] + (p["end"] - p["start"]) * i / n for i in range(n)]
    bounds = [(i, char_t[i]) for i in _word_bounds(full)]
    bad = set()
    for kw in keep:
        i = full.find(kw)
        if i >= 0:
            bad.update(range(i + 1, i + len(kw)))
    cand = [(b, t) for b, t in bounds[1:] if b not in bad]
    total_w = _text_width(_clean(full))
    n = int(-(-total_w // max_chars))            # 需要拆成几条（向上取整）
    out, start, t0 = [], 0, p["start"]
    for k in range(1, n):
        target = total_w * k / n                  # 尽量平均分，避免最后剩下半个词
        opts = [(b, t) for b, t in cand if b > start + 1 and _text_width(_clean(full[start:b])) <= max_chars + 0.5]
        if not opts:
            break

        def score(bt):
            b = bt[0]
            sc = abs(_text_width(_clean(full[:b])) - target)
            if full[b - 1] in _BREAK_AFTER:       # 标点后面断最自然
                sc -= 1.5
            return sc
        b, t = min(opts, key=score)
        if _text_width(_clean(full[b:])) < 2.5:
            break
        out.append((t0, t, full[start:b]))
        start, t0 = b, t
    out.append((t0, p["end"], full[start:]))
    return [(a, b, x) for a, b, x in out if _clean(x)]


def build_events(ranges, durs, plan):
    """把方案换算到成片时间：字幕行、大字弹出、音效点（已考虑整体变速）。"""
    emph = {e["id"]: e for e in plan.get("emphasis", [])}
    st = SET.of(plan)
    sp = st["speed"]
    max_chars = min(st["sub"]["max_chars"], 9) if st["layout"] == "text" else st["sub"]["max_chars"]
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
            color = (e or {}).get("color", "yellow")
            pieces = split_phrase(p, plan, max_chars, [k for k, _ in kws])
            for k, (ca, cb, ctext) in enumerate(pieces):
                sa = off + max(0.0, ca - r["start"]) / sp
                sb = b if k == len(pieces) - 1 else off + max(0.0, cb - r["start"]) / sp
                sa = max(0.0, max(a, sa) - SUB_LEAD)
                if sb <= sa:
                    continue
                subs.append({"a": sa, "b": sb, "text": ctext, "color": color,
                             "kws": [(kw, kt) for kw, kt in kws if kw in ctext]})
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
    # 字幕不重叠：上一条在下一条出现前结束（居中版式里重叠会很明显）
    subs.sort(key=lambda x: x["a"])
    for s1, s2 in zip(subs, subs[1:]):
        if s1["b"] > s2["a"]:
            s1["b"] = max(s1["a"] + 0.2, s2["a"])
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


def phrase_times(ranges, durs, plan):
    """每句话在成片里的时间 {句号: (开始, 结束)}（钩子复制段不算）。"""
    sp = SET.of(plan)["speed"]
    out, off = {}, 0.0
    for r, real in zip(ranges, durs):
        if not r.get("hook"):
            for p in r["phrases"]:
                a = off + max(0.0, p["start"] - r["start"]) / sp
                b = min(off + real, off + (p["end"] - r["start"]) / sp)
                out.setdefault(p["id"], (a, b))
        off += real
    return out


def build_points(ranges, durs, plan, total):
    """分点卡片组 → 成片时间：每个要点在说到那句时出现，整组在最后一点说完 1.2 秒后收起。"""
    pt = phrase_times(ranges, durs, plan)
    groups = []
    for g in plan.get("points") or []:
        items = [dict(text=it["text"], a=pt[it["id"]][0], b=pt[it["id"]][1])
                 for it in g.get("items", []) if it.get("id") in pt and it.get("text")]
        if len(items) < 2:
            continue
        items.sort(key=lambda x: x["a"])
        groups.append({"title": g.get("title", ""), "color": g.get("color"), "items": items,
                       "a": max(0.0, items[0]["a"] - 0.2), "b": min(total, items[-1]["b"] + 1.2)})
    groups.sort(key=lambda g: g["a"])
    for g1, g2 in zip(groups, groups[1:]):
        g1["b"] = min(g1["b"], g2["a"] - 0.05)
    return groups


def write_ass(path, subs, pops, title, total, meta, font, st, points=(), ending=""):
    Path(path).write_text(typo.build_ass(subs, pops, title, total, meta, font, st, points, ending),
                          encoding="utf-8-sig")


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
        bgm_on = bool(BGM_FILES.get(bgm_kind)) and (BGM_DIR / BGM_FILES.get(bgm_kind, "x")).is_file()
        duck = st["audio"]["duck"] and bgm_on
        parts.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo" + (",asplit=2[a0][vk]" if duck else "[a0]"))
        labels.append("[a0]")
    else:
        duck = False
    idx = 1
    for k, s in enumerate(sfx):
        f = SFX_DIR / s["file"]
        if not f.is_file():
            continue
        cmd += ["-i", str(f)]
        ms = int(s["t"] * 1000)
        parts.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume={SFX_VOL[s['kind']] * st['audio']['sfx']:.3f},adelay={ms}|{ms}[s{k}]")
        labels.append(f"[s{k}]")
        idx += 1
    bgm = BGM_FILES.get(bgm_kind)
    if bgm and (BGM_DIR / bgm).is_file():
        cmd += ["-stream_loop", "-1", "-i", str(BGM_DIR / bgm)]
        fo = max(0.0, total - 2.0)
        vol = st["audio"]["bgm"] or bgm_vol
        parts.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={vol:.3f},"
                     f"atrim=0:{total:.3f},afade=t=in:d=1,afade=t=out:st={fo:.3f}:d=2" + ("[bgr]" if duck else "[bg]"))
        if duck:   # 音频闪避：人声一出来，音乐自动压低
            parts.append("[bgr][vk]sidechaincompress=threshold=0.035:ratio=5:attack=15:release=350[bg]")
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
    vf = f"{vin}{ass_filter(workdir, 'subs.ass')}"
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
        bottom = H - H * 0.26 - S * 1.7
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
    text_mode = st["layout"] == "text" or not src_meta.get("has_video", True)
    if text_mode:
        st["layout"] = "text"
        W, H = SET.CANVASES.get(st["canvas"]) or (1080, 1920)
    else:
        W, H = SET.canvas_size(src_meta, st)
    meta = dict(src_meta, w=W, h=H, has_audio=src_meta["has_audio"])   # 之后的字幕/配图都按成片画布算
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
        prog = (lambda x: progress(0.75 * x)) if progress else None
        if text_mode:
            log("纯文字视频：剪人声，生成背景…")
            body, durs = render_text_body(src, ranges, src_meta, tmp, st, W, H, prog)
        else:
            body, durs = render_segments(src, ranges, src_meta, tmp, log, prog, st, (W, H))
        subs, pops, sfx, total = build_events(ranges, durs, plan)
        points = build_points(ranges, durs, plan, total)
        write_ass(tmp / "subs.ass", subs, pops, plan.get("title", ""), total, meta, font, st,
                  points, plan.get("ending", ""))
        overlays = []
        if broll_dir and not text_mode:
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
    if st["layout"] == "text" or not src_meta.get("has_video", True):
        W, H = SET.CANVASES.get(st["canvas"]) or (1080, 1920)
    else:
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
Style: C,{font_roles(font)["heavy"]},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,2,0,1,{max(4, fs // 11)},{max(3, fs // 16)},8,{round(W * 0.05)},{round(W * 0.05)},{round(H * (0.16 if portrait else 0.1))},1

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
        if st["layout"] == "text" or not src_meta.get("has_video", True):   # 纯文字视频：用生成的背景
            W, H = SET.CANVASES.get(st["canvas"]) or (1080, 1920)
            bg = tmp / "bg.mp4"
            make_background(bg, W, H, 1.0, st)
            src, t = bg, 0.5
            vf = f"[0:v]scale={W}:{H}," + ass_filter(tmp, "cover.ass") + "[v]"
        else:
            vf = SET.video_filters(src_meta, st, W, H, "[0:v]", "") + "," + ass_filter(tmp, "cover.ass") + "[v]"
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
                        "-i", str(Path(src).resolve()), "-frames:v", "1", "-filter_complex", vf, "-map", "[v]",
                        "-q:v", "2", str(Path(out_jpg).resolve())], check=True, cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out_jpg)
