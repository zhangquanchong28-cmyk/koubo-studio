# -*- coding: utf-8 -*-
"""全片设置（变速、画面比例、调色、字幕样式、声音、淡入淡出、导出）：默认值、预设和校验。
存放在方案 plan["settings"] 里，出片 / 剪映草稿 / 网页预览共用。"""
import re

CANVASES = {"original": None, "9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "3:4": (1080, 1440)}
SUB_STYLES = ["outline", "box", "shadow"]
RESOLUTIONS = {"original": None, "1080": 1080, "720": 720}
QUALITY_CRF = {"high": 18, "standard": 23}

# 滤镜预设：直接给出各项数值（网页上选预设就是把滑块拨到这些值）
COLOR_PRESETS = {
    "none": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "temp": 6500, "sharpen": 0.0},
    "fresh": {"brightness": 0.03, "contrast": 1.02, "saturation": 1.12, "temp": 6900, "sharpen": 0.3},
    "warm": {"brightness": 0.02, "contrast": 1.02, "saturation": 1.06, "temp": 5300, "sharpen": 0.2},
    "cool": {"brightness": 0.0, "contrast": 1.04, "saturation": 0.98, "temp": 7800, "sharpen": 0.2},
    "film": {"brightness": -0.01, "contrast": 1.06, "saturation": 0.78, "temp": 6200, "sharpen": 0.2},
    "bright": {"brightness": 0.06, "contrast": 1.03, "saturation": 1.04, "temp": 6500, "sharpen": 0.3},
}

DEFAULT = {
    "speed": 1.0,
    "canvas": "original",
    "color": dict(COLOR_PRESETS["none"], preset="none"),
    "sub": {"size": 1.0, "pos": 0, "style": "outline", "color": "#FFFFFF"},   # pos=0 表示自动
    "audio": {"denoise": False, "loudnorm": True},
    "fade": {"in": False, "out": True},
    "export": {"res": "original", "quality": "high"},
}


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def normalize(raw):
    """把任意输入校正成合法设置（缺的用默认值）。"""
    raw = raw if isinstance(raw, dict) else {}
    out = {"speed": round(_num(raw.get("speed"), 0.5, 2.0, 1.0), 2),
           "canvas": raw.get("canvas") if raw.get("canvas") in CANVASES else "original"}
    c = raw.get("color") if isinstance(raw.get("color"), dict) else {}
    d = DEFAULT["color"]
    out["color"] = {
        "preset": c.get("preset") if c.get("preset") in COLOR_PRESETS else "none",
        "brightness": round(_num(c.get("brightness"), -0.3, 0.3, d["brightness"]), 3),
        "contrast": round(_num(c.get("contrast"), 0.5, 1.8, d["contrast"]), 3),
        "saturation": round(_num(c.get("saturation"), 0.0, 2.0, d["saturation"]), 3),
        "temp": int(_num(c.get("temp"), 3000, 10000, d["temp"])),
        "sharpen": round(_num(c.get("sharpen"), 0.0, 1.5, d["sharpen"]), 2),
    }
    s = raw.get("sub") if isinstance(raw.get("sub"), dict) else {}
    col = str(s.get("color") or "#FFFFFF")
    out["sub"] = {"size": round(_num(s.get("size"), 0.6, 1.8, 1.0), 2),
                  "pos": round(_num(s.get("pos"), 0, 45, 0), 1),
                  "style": s.get("style") if s.get("style") in SUB_STYLES else "outline",
                  "color": col.upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", col) else "#FFFFFF"}
    a = raw.get("audio") if isinstance(raw.get("audio"), dict) else {}
    out["audio"] = {"denoise": bool(a.get("denoise", False)), "loudnorm": bool(a.get("loudnorm", True))}
    f = raw.get("fade") if isinstance(raw.get("fade"), dict) else {}
    out["fade"] = {"in": bool(f.get("in", False)), "out": bool(f.get("out", True))}
    e = raw.get("export") if isinstance(raw.get("export"), dict) else {}
    out["export"] = {"res": e.get("res") if e.get("res") in RESOLUTIONS else "original",
                     "quality": e.get("quality") if e.get("quality") in QUALITY_CRF else "high"}
    return out


def of(plan):
    return normalize((plan or {}).get("settings"))


def canvas_size(meta, st):
    """成片画布尺寸。原始比例时就是原片尺寸；换比例时按目标比例，长边不超过原片长边。"""
    target = CANVASES.get(st["canvas"])
    W, H = meta["w"], meta["h"]
    if not target:
        return W, H
    tw, th = target
    if abs(tw / th - W / H) < 0.01:
        return W, H
    scale = min(1.0, max(W, H) / max(tw, th))
    return int(round(tw * scale / 2)) * 2, int(round(th * scale / 2)) * 2


def video_filters(meta, st, W, H, label_in="[0:v]", label_out="[vb]"):
    """画布（含模糊背景）+ 调色 的 filter_complex 片段。"""
    sw, sh = meta["w"], meta["h"]
    if (W, H) != (sw, sh) and abs(W / H - sw / sh) > 0.01:
        base = (f"{label_in}split[cbg][cfg];"
                f"[cbg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:3,eq=brightness=-0.08[cbg2];"
                f"[cfg]scale={W}:{H}:force_original_aspect_ratio=decrease[cfg2];"
                f"[cbg2][cfg2]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        base = f"{label_in}scale={W}:{H},setsar=1"
    c = st["color"]
    if c["brightness"] or c["contrast"] != 1 or c["saturation"] != 1:
        base += f",eq=brightness={c['brightness']}:contrast={c['contrast']}:saturation={c['saturation']}"
    if c["temp"] != 6500:
        base += f",colortemperature=temperature={c['temp']}"
    if c["sharpen"] > 0:
        base += f",unsharp=5:5:{c['sharpen']:.2f}:5:5:0"
    return base + label_out


def ass_color(hex_rgb, alpha=0):
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()
