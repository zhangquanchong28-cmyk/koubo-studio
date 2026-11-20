# -*- coding: utf-8 -*-
"""文字排版与动画（网感字幕引擎）：生成 ASS 字幕里所有文字相关的行。

参考剪映内置文字动画（入场 145 种 / 出场 97 种 / 循环 93 种）里口播常用、克制的那部分：
  正文入场：渐显 fade、向上滑动 rise、羽化向右擦开 wipe、轻微放大 pop、模糊 blur、
            打字光标 type、逐字显影 char、逐字弹跳 bounce
  正文出场：渐隐 fade、模糊消散 blur、无 none
  重点词：  变色放大 color、底色划重点 marker、下划线擦出 underline
  大字花字：放大弹出 pop、数字滚动 count、3D 卡片 card、发光 glow、叠影立体 stack、打字机 type
  片头：    大标题收拢入场 → 缩到顶部变成常驻标题
  片尾：    引导语逐字弹跳 + 下划线擦出
  分点卡片：编号卡片依次滑入 + 打字机出字
版式：talk=真人口播（字幕在下方安全区）；text=纯文字视频（文字居中放大）。
"""
import re
from functools import lru_cache
from pathlib import Path

import settings as SET

APP = Path(__file__).resolve().parent
FONTS_DIR = APP / "fonts"
BUILTIN = "Source Han Sans CN"
EM = 1 / 1.448          # libass 字号 → 字体 em（用思源黑体实测校准）
HEX = {"yellow": "#FFB800", "red": "#FF2B2B", "blue": "#2E7BFF", "orange": "#FF7A00", "green": "#00B4A0"}
DARK_TEXT = {"yellow", "orange", "green"}       # 这些底色上用深色字
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
BLINK = "{\\alpha&H00&\\t(300,301,\\alpha&HFF&)\\t(600,601,\\alpha&H00&)\\t(900,901,\\alpha&HFF&)}▍"


# ---------------------------------------------------------------- 基础工具
def t_ass(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def esc(t):
    return str(t).replace("{", "（").replace("}", "）").replace("\\", "/")


def clean(text):
    t = re.sub(r"(?<=\d)\.(?=\d)", "\u2024", str(text).strip())   # 先保护小数点（0.25% 不能变成 0 25%）
    for ch in "，。！？；、,.!?;：:":
        t = t.replace(ch, " ")
    return " ".join(t.split()).replace("\u2024", ".")


def c(hexrgb):
    """#RRGGBB → ASS 颜色标签值 &HBBGGRR&"""
    h = hexrgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def accent(name):
    return HEX.get(name, HEX["yellow"])


def font_roles(font):
    f = (font or "").strip()
    if not f or f.startswith(BUILTIN) or f.startswith("思源黑体"):
        return {"body": f"{BUILTIN} Medium", "bold": f"{BUILTIN} Bold", "heavy": f"{BUILTIN} Heavy"}
    return {"body": f, "bold": f, "heavy": f}


def _font_file(name):
    for w in ("Medium", "Bold", "Heavy"):
        if name == f"{BUILTIN} {w}":
            p = FONTS_DIR / f"SourceHanSansCN-{w}.otf"
            return p if p.is_file() else None
    return None


@lru_cache(maxsize=64)
def _pil(path, px):
    from PIL import ImageFont
    return ImageFont.truetype(path, max(1, int(round(px))))


def text_w(text, font, size, spacing=0):
    """文字在画面上的宽度（像素）。内置字体用 Pillow 精确测量，其他字体按字数估算。"""
    f = _font_file(font)
    if f:
        try:
            return _pil(str(f), size * EM).getlength(text) + spacing * len(text)
        except Exception:   # 没装 Pillow 等：退回估算
            pass
    return sum(size * EM * (1.0 if ord(ch) > 0x2E80 else 0.55) for ch in text) + spacing * len(text)


def round_rect(w, h, r):
    r = max(0.0, min(r, h / 2, w / 2))
    k = r * 0.45
    return (f"m {r:.1f} 0 l {w - r:.1f} 0 b {w - k:.1f} 0 {w:.1f} {k:.1f} {w:.1f} {r:.1f} l {w:.1f} {h - r:.1f} "
            f"b {w:.1f} {h - k:.1f} {w - k:.1f} {h:.1f} {w - r:.1f} {h:.1f} l {r:.1f} {h:.1f} b {k:.1f} {h:.1f} 0 {h - k:.1f} "
            f"0 {h - r:.1f} l 0 {r:.1f} b 0 {k:.1f} {k:.1f} 0 {r:.1f} 0")


def circle(d):
    return round_rect(d, d, d / 2)


def dlg(layer, a, b, style, text):
    return f"Dialogue: {layer},{t_ass(a)},{t_ass(b)},{style},,0,0,0,,{text}"


# ---------------------------------------------------------------- 版式
class Layout:
    """所有位置都从这里算，口播 / 纯文字两套。"""

    def __init__(self, W, H, st):
        self.W, self.H = W, H
        self.st = st
        self.portrait = H >= W
        self.text_mode = st.get("layout") == "text"
        self.light = self.text_mode and st.get("bg") == "light"
        self.S = round(min(W, H) * 0.058)
        sub = st["sub"]
        self.SS = round(self.S * sub["size"] * (1.85 if self.text_mode else 1.0))
        if self.text_mode:
            self.sub_y = round(H * 0.56)            # 居中（an5）
            self.sub_alt_y = round(H * 0.84)        # 分点卡片出现时字幕让到下方
            self.pop_y = round(H * 0.32)
        else:
            pos = sub["pos"] / 100 if sub["pos"] else (0.26 if self.portrait else 0.08)
            self.sub_y = round(H * (1 - pos))       # 底边（an2）
            self.sub_alt_y = self.sub_y
            self.pop_y = round(H * (0.27 if self.portrait else 0.16))
        self.title_y = round(H * (0.07 if self.portrait else 0.05))
        self.max_chars = min(sub["max_chars"], int(W * 0.86 / max(1, self.SS * EM * 1.02)))

    def sub_anchor(self):
        return 5 if self.text_mode else 2

    def box_top(self, y):
        """一行字幕的上沿（按锚点换算）。"""
        return y - self.SS * 0.5 if self.text_mode else y - self.SS * 0.98


# ---------------------------------------------------------------- 正文字幕
def _chars_with_kw(text, kws):
    """把一条字幕拆成逐字列表，标出哪些字属于哪个重点词。"""
    marks = [None] * len(text)
    for k, (kw, _kt) in enumerate(kws):
        i = text.find(kw)
        if i >= 0 and all(m is None for m in marks[i:i + len(kw)]):
            for j in range(i, i + len(kw)):
                marks[j] = k
    return marks


def sub_lines(s, L, fr, anim_in, anim_out, kw_style, y=None):
    """一条正文字幕 → 若干 ASS 行（正文 + 划重点的底色/下划线 + 打字光标）。"""
    a, b = s["a"], s["b"]
    dur = b - a
    text = clean(s["text"])
    if not text:
        return []
    y = L.sub_y if y is None else y
    an = L.sub_anchor()
    x = L.W // 2
    kws = [(clean(k), t) for k, t in s["kws"] if clean(k) and clean(k) in text]
    marks = _chars_with_kw(text, kws)
    body, bold = (fr["bold"], fr["heavy"]) if L.text_mode else (fr["body"], fr["bold"])
    sp = 1
    # 每个字的宽度 → 行宽、重点词位置
    widths = [text_w(ch, bold if marks[i] is not None else body, L.SS, sp) for i, ch in enumerate(text)]
    line_w = sum(widths)
    x0 = x - line_w / 2
    col_name = s.get("color", "yellow")
    col = accent(col_name)
    out = []
    multi = line_w > L.W * 0.92                 # 太长会自动换行（少见）：划重点退回变色
    if multi and kw_style != "color":
        kw_style = "color"

    # 每个字的入场时间（逐字类动画）
    n = len(text)
    per_char = anim_in in ("type", "char", "bounce")
    step = min(0.06 if anim_in == "type" else 0.045, max(0.015, dur * 0.55 / max(1, n)))
    char_fx = ""                     # \r 会清掉的逐字级动画，重置后要补回
    if anim_in == "pop":
        char_fx = "\\fscx82\\fscy82\\t(0,160,\\fscx104\\fscy104)\\t(160,240,\\fscx100\\fscy100)"
    elif anim_in == "blur":
        char_fx = "\\blur10\\t(0,260,\\blur0)"
    if anim_out == "blur":
        ms = int(dur * 1000)
        char_fx += f"\\t({max(0, ms - 180)},{ms},\\blur6)"
    pieces, cur_tag = [], None
    for i, ch in enumerate(text):
        k = marks[i]
        tags = ""
        if k is not None:
            kt_ms = int(max(0.0, kws[k][1] - a) * 1000)
            if kw_style == "color":
                tags += (f"\\fn{bold}\\c{c(col)}\\fscx100\\fscy100\\t({kt_ms},{kt_ms + 150},\\fscx116\\fscy116)"
                         f"\\t({kt_ms + 150},{kt_ms + 300},\\fscx110\\fscy110)")
            elif kw_style == "marker":
                fg = "#151515" if col_name in DARK_TEXT else "#FFFFFF"
                tags += f"\\fn{bold}\\t({kt_ms + 200},{kt_ms + 240},\\c{c(fg)}\\3a&HFF&\\4a&HFF&)"   # 底色擦完再换字色
            else:   # underline
                tags += f"\\fn{bold}\\c{c(col)}"
        if per_char:
            ti = int(i * step * 1000)
            if anim_in == "type":
                reveal = f"\\alpha&HFF&\\t({ti},{ti + 1},\\alpha&H00&)"
            elif anim_in == "char":
                reveal = f"\\alpha&HFF&\\t({ti},{ti + 140},\\alpha&H00&)"
            else:   # bounce 逐字弹跳
                reveal = (f"\\alpha&HFF&\\fscy40\\t({ti},{ti + 90},\\alpha&H00&\\fscy118)"
                          f"\\t({ti + 90},{ti + 190},\\fscy100)")
            # 逐字动画：每个字先重置（避免重点词样式漏到后面的字），显现动画放前面、重点词样式放后面（后者才会生效）
            tags = "\\r" + char_fx + reveal + tags
        tag = "{" + tags + "}" if tags else ""
        if not per_char and tags == cur_tag:
            pieces.append(esc(ch))
        else:
            if cur_tag and not per_char:
                pieces.append("{\\r" + char_fx + "}")
            pieces.append(tag + esc(ch))
        cur_tag = tags if tags else None
    rich = "".join(pieces)
    lead = f"\\an{an}\\pos({x},{y})"
    fin = 150 if anim_in in ("fade", "rise", "pop", "blur", "wipe") else 60
    fout = {"fade": 80, "blur": 140, "none": 0}.get(anim_out, 80)
    fx = f"\\fad({fin},{fout})"
    if anim_in == "rise":
        dy = round(L.SS * 0.55)
        lead = f"\\an{an}\\move({x},{y + dy},{x},{y},0,220)"
    elif anim_in == "wipe":
        top, bot = L.box_top(y) - L.SS * 0.4, L.box_top(y) + L.SS * 1.4
        fx += (f"\\clip({x0 - 12:.0f},{top:.0f},{x0 - 11:.0f},{bot:.0f})"
               f"\\t(0,300,\\clip({x0 - 12:.0f},{top:.0f},{x0 + line_w + 14:.0f},{bot:.0f}))")
    fx += char_fx
    out.append(dlg(2, a, b, "Sub", "{" + lead + fx + "}" + rich))

    # 打字光标：跟着字往右走，打完闪两下
    if anim_in == "type":
        top = L.box_top(y)
        type_end = n * step
        cx0, cx1 = x0, x0 + line_w
        bar_h = L.SS * 0.95
        cur_col = "#FFFFFF" if not L.light else "#222222"
        out.append(dlg(3, a, min(b, a + type_end + 0.5), "Fx",
                       f"{{\\an7\\move({cx0:.0f},{top:.0f},{cx1 + 4:.0f},{top:.0f},0,{int(type_end * 1000)})"
                       f"\\bord0\\shad0\\1c{c(cur_col)}\\t({int(type_end * 1000)},{int(type_end * 1000) + 1},\\alpha&HFF&)"
                       f"\\t({int(type_end * 1000) + 200},{int(type_end * 1000) + 201},\\alpha&H00&)\\p1}}"
                       f"{round_rect(max(3, L.SS * 0.07), bar_h, 1)}{{\\p0}}"))

    # 划重点：底色条 / 下划线（从左往右擦出）
    if kw_style in ("marker", "underline"):
        top = L.box_top(y)
        for k, (kw, kt) in enumerate(kws):
            idx = [i for i, m in enumerate(marks) if m == k]
            if not idx:
                continue
            kx = x0 + sum(widths[:idx[0]])
            kw_w = sum(widths[idx[0]:idx[-1] + 1])
            if kw_style == "marker":
                pad = L.SS * 0.12
                rx, ry, rw, rh = kx - pad, top + L.SS * (0.0 if L.text_mode else -0.02), kw_w + pad * 2, L.SS * 0.9
                alpha = "&H00&"
            else:
                rx, ry, rw, rh = kx, top + L.SS * 0.94, kw_w, max(4, L.SS * 0.1)
                alpha = "&H00&"
            ms0 = int(max(0.0, kt - a) * 1000)
            clip_a = f"\\clip({rx - 2:.0f},{ry - 4:.0f},{rx - 1:.0f},{ry + rh + 4:.0f})"
            clip_b = f"\\clip({rx - 2:.0f},{ry - 4:.0f},{rx + rw + 2:.0f},{ry + rh + 4:.0f})"
            out.append(dlg(1, a, b, "Fx",
                           f"{{\\an7\\pos({rx:.0f},{ry:.0f})\\fad(0,{fout})\\bord0\\shad0\\1c{c(col)}\\1a{alpha}"
                           f"{clip_a}\\t({ms0},{ms0 + 220},{clip_b})\\p1}}{round_rect(rw, rh, rh * 0.22)}{{\\p0}}"))
    return out


# ---------------------------------------------------------------- 大字花字
def pop_lines(p, L, fr):
    fx = p.get("effect", "pop")
    a, b = p["a"], p["b"]
    text = esc(clean(p["text"]))
    col = accent(p.get("color", "yellow"))
    x, y = L.W // 2, L.pop_y
    S = L.S
    fs = round(S * 2.1)
    base = f"\\an8\\pos({x},{y})\\fn{fr['heavy']}\\fs{fs}\\fsp3"
    popin = "\\fscx40\\fscy40\\t(0,160,\\fscx114\\fscy114)\\t(160,280,\\fscx100\\fscy100)"
    if fx == "count" and NUM_RE.search(clean(p["text"])):
        return _count(p, L, fr)
    if fx == "card":
        return _card(p, L, fr)
    if fx == "glow":      # 发光字：白字 + 彩色光晕
        glow = (f"{{{base}\\fad(80,200){popin}\\1a&HFF&\\3c{c(col)}\\bord{round(fs * 0.22)}\\blur{round(fs * 0.22)}"
                f"\\shad0}}{text}")
        core = (f"{{{base}\\fad(80,200){popin}\\1c&HFFFFFF&\\3c{c(col)}\\bord{max(2, fs // 40)}\\shad0}}{text}")
        return [dlg(4, a, b, "Fx", glow), dlg(5, a, b, "Fx", core)]
    if fx == "stack":     # 叠影立体字：白字粗彩边 + 深色错位投影
        off = max(4, round(fs * 0.07))
        back = (f"{{\\an8\\pos({x + off},{y + off})\\fn{fr['heavy']}\\fs{fs}\\fsp3\\fad(80,200){popin}"
                f"\\1c&H1A1A1A&\\3c&H1A1A1A&\\bord{round(fs * 0.09)}\\shad0}}{text}")
        front = (f"{{{base}\\fad(80,200){popin}\\1c&HFFFFFF&\\3c{c(col)}\\bord{round(fs * 0.09)}\\shad0}}{text}")
        return [dlg(4, a, b, "Fx", back), dlg(5, a, b, "Fx", front)]
    if fx == "type":      # 打字机大字：左对齐逐字打出 + 闪烁光标
        raw = clean(p["text"])
        w = text_w(raw, fr["heavy"], fs, 3)
        x0 = x - w / 2
        out, n = [], len(raw)
        step = min(0.09, max(0.04, (b - a) * 0.5 / max(1, n)))
        for i in range(1, n + 1):
            t0 = a + (i - 1) * step
            t1 = a + i * step if i < n else b
            cursor = BLINK if i == n else "{\\alpha&H00&}▍"
            fade = "\\fad(0,200)" if i == n else ""
            out.append(dlg(4, t0, t1, "Fx", f"{{\\an7\\pos({x0:.0f},{y})\\fn{fr['heavy']}\\fs{fs}\\fsp3\\1c{c(col)}"
                                            f"\\3c&H000000&\\bord{max(2, fs // 30)}\\shad{max(1, fs // 40)}"
                                            f"{fade}}}{esc(raw[:i])}{cursor}"))
        return out
    # pop：放大弹出
    return [dlg(4, a, b, "Fx", f"{{{base}\\fad(60,180){popin}\\1c{c(col)}\\3c&H000000&\\bord{max(3, fs // 22)}"
                               f"\\shad{max(2, fs // 30)}}}{text}")]


def _count(p, L, fr):
    text = clean(p["text"])
    m = NUM_RE.search(text)
    pre, num, post = text[:m.start()], m.group(0), text[m.end():]
    target = float(num)
    dec = len(num.split(".")[1]) if "." in num else 0
    col = accent(p.get("color", "yellow"))
    a, b = p["a"], p["b"]
    roll = min(0.7, max(0.3, (b - a) * 0.5))
    fs = round(L.S * 2.1)
    pos = f"\\an8\\pos({L.W // 2},{L.pop_y})\\fn{fr['heavy']}\\fs{fs}\\fsp3\\1c{c(col)}\\3c&H000000&\\bord{max(3, fs // 22)}\\shad{max(2, fs // 30)}"
    out, steps = [], 16
    for i in range(steps):
        k = 1 - (1 - i / steps) ** 3
        v = target * k
        shown = f"{v:.{dec}f}" if dec else str(int(round(v)) if i == steps - 1 else int(v))
        t0, t1 = a + i * roll / steps, a + (i + 1) * roll / steps
        fade = "\\fad(60,0)" if i == 0 else ""
        out.append(dlg(4, t0, t1, "Fx", f"{{{pos}{fade}}}{esc(pre)}{shown}{esc(post)}"))
    out.append(dlg(4, a + roll, b, "Fx", f"{{{pos}\\fad(0,180)\\t(0,110,\\fscx118\\fscy118)\\t(110,260,\\fscx100\\fscy100)}}{esc(text)}"))
    return out


def _card(p, L, fr):
    text = clean(p["text"])
    S = L.S
    fs = round(S * 1.7)
    tw = text_w(text, fr["heavy"], fs, 2)
    cw, ch = round(tw + fs * 1.2), round(fs * 1.7)
    cx, cy = L.W // 2, L.pop_y + round(ch * 0.6 + S * 0.35)
    name = p.get("color", "yellow")
    bg = accent(name)
    fg = "#181818" if name in DARK_TEXT else "#FFFFFF"
    org = f"\\org({cx},{cy})"
    anim = (f"\\fry-80\\frx10\\fscx80\\fscy80\\t(0,240,0.7,\\fry6\\frx2\\fscx106\\fscy106)"
            f"\\t(240,400,\\fry-10\\frx4\\fscx100\\fscy100)\\fad(40,200)")
    a, b = p["a"], p["b"]
    return [
        dlg(4, a, b, "Fx", f"{{\\an5\\pos({cx + fs // 7},{cy + fs // 6}){org}{anim}\\bord0\\shad0\\blur8\\1c&H000000&\\1a&H70&\\p1}}{round_rect(cw, ch, ch * 0.28)}{{\\p0}}"),
        dlg(5, a, b, "Fx", f"{{\\an5\\pos({cx},{cy}){org}{anim}\\bord0\\shad0\\1c{c(bg)}\\p1}}{round_rect(cw, ch, ch * 0.28)}{{\\p0}}"),
        dlg(6, a, b, "Fx", f"{{\\an5\\pos({cx},{cy}){org}{anim}\\fn{fr['heavy']}\\fs{fs}\\fsp2\\1c{c(fg)}\\bord0\\shad0}}{esc(text)}"),
    ]


# ---------------------------------------------------------------- 片头 / 片尾
def split_title(title, per_line):
    """标题折成两行：按词断（jieba），两行尽量一样长。"""
    t = clean(title).replace(" ", "")
    if len(t) <= per_line:
        return [t]
    try:
        import packer
        bounds = [b for b in packer._word_bounds(t) if 0 < b < len(t)]
    except Exception:
        bounds = []
    cut = min(bounds, key=lambda b: abs(b - len(t) / 2)) if bounds else (len(t) + 1) // 2
    if t[cut:cut + 1] in "的了着地得之" and cut + 1 < len(t):   # 第二行别用"的""了"开头
        cut += 1
    return [t[:cut], t[cut:]]


def intro_lines(title, L, fr, col_name, total):
    """片头：大标题收拢入场（模糊+缩放），停一下，再飞到顶部缩成常驻标题。返回 (行, 常驻标题开始时间)。"""
    if not title or total < 4:
        return [], 0.0
    lines = split_title(title, 8 if L.portrait else 14)
    fs = round(L.S * 2.15)
    hold = min(2.2, max(1.4, total * 0.12))
    fly = 0.4
    cx, cy = L.W // 2, round(L.H * (0.40 if not L.text_mode else 0.36))
    ty = L.title_y + round(L.S * 0.6)
    scale = round(100 * 1.1 / 2.15)
    out = []
    lh = fs * 1.15
    y0 = cy - lh * (len(lines) - 1) / 2
    col = accent(col_name)
    for i, ln in enumerate(lines):
        y = y0 + i * lh
        ty_i = ty + (i - (len(lines) - 1) / 2) * lh * scale / 100
        fill = "#FFFFFF" if i == 0 else col
        delay = 120 * i
        out.append(dlg(7, 0, hold + fly, "Fx",
                       f"{{\\an5\\move({cx},{y:.0f},{cx},{ty_i:.0f},{int(hold * 1000)},{int((hold + fly) * 1000)})"
                       f"\\fn{fr['heavy']}\\fs{fs}\\fsp4\\1c{c(fill)}\\3c&H000000&\\bord{max(3, fs // 24)}\\shad{max(2, fs // 30)}"
                       f"\\alpha&HFF&\\fscx135\\fscy135\\blur14"
                       f"\\t({delay},{delay + 380},\\alpha&H00&\\fscx100\\fscy100\\blur0)"
                       f"\\t({int(hold * 1000)},{int((hold + fly) * 1000)},\\fscx{scale}\\fscy{scale})"
                       f"\\t({int((hold + fly - 0.12) * 1000)},{int((hold + fly) * 1000)},\\alpha&HFF&)}}{esc(ln)}"))
    # 标题下方一条强调色短横线，擦出后随标题一起淡出
    bw = text_w(lines[-1], fr["heavy"], fs, 4) * 0.5
    by = y0 + (len(lines) - 1) * lh + fs * 0.62
    out.append(dlg(6, 0.25, hold, "Fx",
                   f"{{\\an7\\pos({cx - bw / 2:.0f},{by:.0f})\\bord0\\shad0\\1c{c(col)}\\fad(0,200)"
                   f"\\clip({cx - bw / 2 - 1:.0f},{by - 2:.0f},{cx - bw / 2:.0f},{by + 20:.0f})"
                   f"\\t(150,500,\\clip({cx - bw / 2 - 1:.0f},{by - 2:.0f},{cx + bw / 2 + 1:.0f},{by + 20:.0f}))\\p1}}"
                   f"{round_rect(bw, max(6, fs * 0.09), 3)}{{\\p0}}"))
    return out, hold + fly - 0.1


def outro_lines(text, L, fr, col_name, total):
    """片尾：引导语逐字弹跳出现 + 下划线擦出（最后约 2.6 秒）。"""
    text = clean(text)
    if not text or total < 6:
        return []
    a = max(0.0, total - 2.6)
    fs = round(L.S * 1.25)
    cx, cy = L.W // 2, round(L.H * (0.40 if not L.text_mode else 0.30))
    col = accent(col_name)
    chars = []
    for i, ch in enumerate(text):
        ti = i * 55
        chars.append(f"{{\\alpha&HFF&\\fscy40\\t({ti},{ti + 90},\\alpha&H00&\\fscy118)\\t({ti + 90},{ti + 190},\\fscy100)}}{esc(ch)}")
    fill = "#222222" if L.light else "#FFFFFF"
    out = [dlg(7, a, total, "Fx", f"{{\\an5\\pos({cx},{cy})\\fn{fr['bold']}\\fs{fs}\\fsp2\\1c{c(fill)}\\3c&H000000&"
                                  f"\\bord{0 if L.light else max(2, fs // 28)}\\shad{0 if L.light else max(1, fs // 30)}}}"
                                  + "".join(chars))]
    w = text_w(text, fr["bold"], fs, 2)
    by = cy + fs * 0.7
    t0 = len(text) * 55 + 150
    out.append(dlg(6, a, total, "Fx",
                   f"{{\\an7\\pos({cx - w / 2:.0f},{by:.0f})\\bord0\\shad0\\1c{c(col)}"
                   f"\\clip({cx - w / 2 - 1:.0f},{by - 2:.0f},{cx - w / 2:.0f},{by + 20:.0f})"
                   f"\\t({t0},{t0 + 350},\\clip({cx - w / 2 - 1:.0f},{by - 2:.0f},{cx + w / 2 + 1:.0f},{by + 20:.0f}))\\p1}}"
                   f"{round_rect(w, max(6, fs * 0.1), 3)}{{\\p0}}"))
    return out


# ---------------------------------------------------------------- 分点卡片
def points_lines(g, L, fr, col_name):
    """一组分点：标题 + 编号卡片依次滑入，卡片文字打字机出字，整组一起淡出。"""
    items = [it for it in g["items"] if it.get("text")]
    if not items:
        return []
    S = L.S
    big = 1.25 if L.text_mode else 1.0
    fs = round(S * 1.05 * big)
    ch = round(S * 1.8 * big)
    gap = round(S * 0.36 * big)
    cw = round(L.W * (0.86 if L.text_mode else 0.82))
    x = (L.W - cw) // 2
    n = len(items)
    title = clean(g.get("title") or "")
    title_h = round(S * 1.75 * big) if title else 0
    stack_h = title_h + n * ch + (n - 1) * gap
    if L.text_mode:
        top = round(L.H * 0.44 - stack_h / 2)
    else:
        top = round(L.box_top(L.sub_y) - S * 1.0 - stack_h)
    col = accent(col_name)
    a_end = g["b"]
    out = []
    card_bg = "#FFFFFF"
    text_col = "#1A1A1A"
    if title:
        ta = g["a"]
        out.append(dlg(6, ta, a_end, "Fx", f"{{\\an7\\move({x},{top + 18},{x},{top},0,260)\\fad(200,250)\\fn{fr['heavy']}"
                                           f"\\fs{round(S * 1.25 * big)}\\fsp2\\1c{c(col)}\\3c&H000000&\\bord{0 if L.light else 2}\\shad{0 if L.light else 2}}}{esc(title)}"))
    for i, it in enumerate(items):
        a = it["a"]
        y = top + title_h + i * (ch + gap)
        slide = round(S * 1.2)
        mv = f"\\move({x - slide},{y},{x},{y},0,260)"
        out.append(dlg(4, a, a_end, "Fx", f"{{\\an7{mv}\\fad(220,250)\\bord0\\shad0\\blur6\\1c&H000000&\\1a&H90&\\p1}}"
                                          f"{round_rect(cw, ch, ch * 0.24)}{{\\p0}}"))
        out.append(dlg(5, a, a_end, "Fx", f"{{\\an7{mv}\\fad(220,250)\\bord0\\shad0\\1c{c(card_bg)}\\1a&H10&\\p1}}"
                                          f"{round_rect(cw, ch, ch * 0.24)}{{\\p0}}"))
        d = round(ch * 0.62)
        bx, by = x + round(ch * 0.19), y + (ch - d) // 2
        out.append(dlg(6, a, a_end, "Fx", f"{{\\an7\\move({bx - slide},{by},{bx},{by},0,260)\\fad(220,250)\\bord0\\shad0\\1c{c(col)}"
                                          f"\\fscx0\\fscy0\\t(180,320,\\fscx112\\fscy112)\\t(320,420,\\fscx100\\fscy100)\\org({bx + d // 2},{by + d // 2})\\p1}}"
                                          f"{circle(d)}{{\\p0}}"))
        num_fg = "#151515" if col_name in DARK_TEXT else "#FFFFFF"
        out.append(dlg(7, a + 0.18, a_end, "Fx", f"{{\\an5\\pos({bx + d // 2},{by + d // 2})\\fad(120,250)\\fn{fr['heavy']}\\fs{round(d * 0.62)}"
                                                 f"\\1c{c(num_fg)}\\bord0\\shad0}}{i + 1}"))
        txt = clean(it["text"])
        tx, ty = bx + d + round(S * 0.4), y + ch // 2
        step = min(0.07, max(0.03, 0.55 / max(1, len(txt))))
        typed = "".join(f"{{\\alpha&HFF&\\t({int((0.26 + j * step) * 1000)},{int((0.26 + j * step) * 1000) + 1},\\alpha&H00&)}}{esc(chr_)}"
                        for j, chr_ in enumerate(txt))
        out.append(dlg(7, a, a_end, "Fx", f"{{\\an4\\pos({tx},{ty})\\fad(0,250)\\fn{fr['bold']}\\fs{fs}\\fsp1\\1c{c(text_col)}\\bord0\\shad0}}{typed}"))
    return out


# ---------------------------------------------------------------- 整份 ASS
def header(L, fr):
    st = L.st
    sub = st["sub"]
    SS = L.SS
    if L.light:
        prim = SET.ass_color("#222222" if sub["color"].upper() == "#FFFFFF" else sub["color"])
        sub_style = f"{prim},&H00FFFFFF,&H00FFFFFF,&H00000000,1,0,0,0,100,100,1,0,1,0,0"
    else:
        prim = SET.ass_color(sub["color"])
        if sub["style"] == "soft":
            sub_style = f"{prim},&H00FFFFFF,&H90000000,&H80000000,1,0,0,0,100,100,1,0,1,{max(1, round(SS / 32))},{max(2, round(SS / 14))}"
        elif sub["style"] == "box":
            sub_style = f"{prim},&H00FFFFFF,&H60000000,&H60000000,1,0,0,0,100,100,1,0,3,{round(SS * 0.22)},0"
        elif sub["style"] == "shadow":
            sub_style = f"{prim},&H00FFFFFF,&H00000000,&H50000000,1,0,0,0,100,100,1,0,1,0,{max(2, round(SS / 10))}"
        else:
            sub_style = f"{prim},&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,1,0,1,{max(2, round(SS / 18))},{max(1, SS // 36)}"
    W, H, S = L.W, L.H, L.S
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{fr['body']},{SS},{sub_style},2,{round(W * 0.06)},{round(W * 0.06)},0,1
Style: Fx,{fr['heavy']},{S},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Title,{fr['bold']},{round(S * 1.1)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,2,0,3,{round(S * 0.35)},0,8,{round(W * 0.08)},{round(W * 0.08)},{L.title_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(subs, pops, title, total, meta, font, st, points=(), ending=""):
    """整份 ASS 文本。st 是 settings.normalize 后的全片设置。"""
    L = Layout(meta["w"], meta["h"], st)
    fr = font_roles(font)
    fxs = st.get("fx", {})
    anim_in, anim_out = fxs.get("sub_in", "fade"), fxs.get("sub_out", "fade")
    kw_style = fxs.get("keyword", "color")
    acc = fxs.get("accent", "yellow")
    lines = []
    title_start = 0.0
    if title and fxs.get("intro", True):
        il, title_start = intro_lines(title, L, fr, acc, total)
        lines += il
    if title:
        lines.append(dlg(2, title_start, total, "Title", f"{{\\fad(250,300)}}{esc(title)}"))
    windows = [(g["a"], g["b"]) for g in points]
    for s in subs:
        busy = L.text_mode and any(a - 0.05 <= s["a"] < b for a, b in windows)
        lines += sub_lines(s, L, fr, anim_in, anim_out, kw_style, y=L.sub_alt_y if busy else None)
    busy = list(windows)
    if title_start:
        busy.append((0.0, title_start + 0.2))          # 片头标题还在画面中间
    if ending and fxs.get("outro", True) and total >= 6:
        busy.append((total - 2.8, total))              # 片尾引导语
    for p in pops:
        if any(a - 0.05 <= p["a"] < b or a < p["b"] <= b for a, b in busy):
            continue          # 片头、片尾、分点卡片出现时不出大字，避免画面打架
        lines += pop_lines(p, L, fr)
    for g in points:
        lines += points_lines(g, L, fr, g.get("color") or acc)
    if ending and fxs.get("outro", True):
        lines += outro_lines(ending, L, fr, acc, total)
    return header(L, fr) + "\n".join(lines) + "\n"
