# -*- coding: utf-8 -*-
"""导出剪映草稿：把 AI 方案变成剪映里可继续精修的工程（每句一段视频、字幕、大字、音效分轨、BGM）。

⚠️ 在 Windows 剪映上没有实测过，用法：
  1. 先完全退出剪映（任务栏右键退出），再点「导出剪映草稿」
  2. 打开剪映，草稿列表里就能看到；看不到就重启剪映
  3. 剪映打开并保存后会把草稿加密（单向）。想重新导，再点一次导出就会覆盖。
字号/位置是近似换算，进剪映后可整轨统一调整。
"""
import copy
import json
import sys
from pathlib import Path

import pyJianYingDraft as jy

import subprocess

from common import find_tool
from packer import (BGM_DIR, BGM_FILES, SFX_DIR, SFX_VOL, ZOOM_IN_SEC, ZOOM_TO,
                    _clean, broll_box, broll_events, build_events, build_timeline, probe)

US = 1_000_000
HEX = {"yellow": "#FFD400", "red": "#FF3B30", "blue": "#3EA6FF"}


def _us(t):
    return int(round(t * US))


def _rgb(hx):
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _ty(y, H):
    """屏幕 y 像素 → 剪映 transform_y（按半高归一，向上为正）。"""
    return 1 - 2 * y / H


def _size(px, H):
    """像素字号 → 剪映字号（约等于画布高度的百分比 ×10）。"""
    return round(px * 108 / H, 1)


def _no_overlap(items):
    """剪映同一轨上的片段不能重叠：每条的结束时间截到下一条开始。"""
    items = sorted(items, key=lambda x: x["a"])
    out = []
    for i, it in enumerate(items):
        b = min(it["b"], items[i + 1]["a"]) if i + 1 < len(items) else it["b"]
        if b - it["a"] >= 0.05:
            out.append(dict(it, b=b))
    return out


def _color_keywords(content_path, subs):
    """字幕里的重点词按方案颜色分段着色（改草稿 JSON 里文字的 styles 区间）。"""
    want = {}
    for s in subs:
        if s["kws"]:
            want[_clean(s["text"])] = ([_clean(k) for k, _ in s["kws"]], _rgb(HEX.get(s["color"], HEX["yellow"])))
    if not want:
        return
    c = json.loads(content_path.read_text(encoding="utf-8"))
    for m in c["materials"].get("texts", []):
        try:
            body = json.loads(m["content"])
        except (KeyError, ValueError):
            continue
        text = body.get("text", "")
        if text not in want or not body.get("styles"):
            continue
        kws, rgb = want[text]
        marks = sorted({(text.find(k), text.find(k) + len(k)) for k in kws if k and k in text})
        base = body["styles"][0]
        cuts, pos = [], 0
        for a, b in marks:
            if a < pos:
                continue
            if a > pos:
                cuts.append((pos, a, False))
            cuts.append((a, b, True))
            pos = b
        if pos < len(text):
            cuts.append((pos, len(text), False))
        styles = []
        for a, b, hot in cuts:
            st = copy.deepcopy(base)
            st["range"] = [a, b]
            if hot:
                st["fill"]["content"]["solid"]["color"] = list(rgb)
            styles.append(st)
        body["styles"] = styles
        m["content"] = json.dumps(body, ensure_ascii=False)
    content_path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


def _img_size(path):
    out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def build_draft(src, data, plan, staging, name, log=print, broll_dir=None):
    meta = probe(src)
    W, H = meta["w"], meta["h"]
    fps = int(round(meta["fpsf"]))
    ranges = build_timeline(data, plan)
    if not ranges:
        raise RuntimeError("方案里没有要保留的内容。")
    # 剪映里按源时长拼接，时间线以微秒取整，避免累计误差
    durs = [(_us(r["end"]) - _us(r["start"])) / US for r in ranges]
    subs, pops, sfx, total = build_events(ranges, durs, plan)
    subs, pops = _no_overlap(subs), _no_overlap(pops)
    total_us = sum(_us(r["end"]) - _us(r["start"]) for r in ranges)

    folder = jy.DraftFolder(str(staging))
    script = folder.create_draft(name, W, H, fps=fps, allow_replace=True)
    for kind, tname in [(jy.TrackType.video, "口播"), (jy.TrackType.audio, "BGM"),
                        (jy.TrackType.text, "字幕"), (jy.TrackType.text, "大字"), (jy.TrackType.text, "标题")]:
        script.append_track(jy.TrackSpec(kind, name=tname))

    # 视频：每段一个片段，源区间=原片位置，可在剪映里单独删改/变速
    mat = jy.VideoMaterial(str(src))
    off = 0
    for r in ranges:
        s0 = _us(r["start"])
        d = _us(r["end"]) - s0
        seg = jy.VideoSegment(mat, jy.Timerange(off, d), source_timerange=jy.Timerange(s0, d))
        if r["zoom"]:
            seg.add_keyframe(jy.KeyframeProperty.uniform_scale, 0, 1.0)
            seg.add_keyframe(jy.KeyframeProperty.uniform_scale, min(d, _us(ZOOM_IN_SEC)), ZOOM_TO)
        script.add_segment(seg, "口播")
        off += d

    # 配图：单独一条视频轨，压在口播上面，可在剪映里直接替换图片
    n_img = 0
    evs = broll_events(ranges, durs, plan, total) if broll_dir else []
    if evs:
        script.append_track(jy.TrackSpec(jy.TrackType.video, name="配图"))
    for ev in evs:
        img = Path(broll_dir) / ev["item"]["image"]
        if not img.is_file():
            continue
        x, y, w, h = broll_box(ev["item"]["layout"], W, H)
        iw, ih = _img_size(img)
        f = min(W / iw, H / ih)                      # 剪映默认把图片“适应”画布
        fw, fh = iw * f, ih * f
        sc = max(w / fw, h / fh) if ev["item"]["layout"] == "full" else w / fw
        a_us = _us(ev["a"])
        d_us = min(_us(ev["b"]), total_us) - a_us
        seg = jy.VideoSegment(jy.VideoMaterial(str(img)), jy.Timerange(a_us, d_us),
                              clip_settings=jy.ClipSettings(scale_x=sc, scale_y=sc,
                                                            transform_x=(x + w / 2 - W / 2) / (W / 2),
                                                            transform_y=(H / 2 - (y + h / 2)) / (H / 2)))
        seg.add_keyframe(jy.KeyframeProperty.uniform_scale, 0, 1.0)   # 缓慢推近
        seg.add_keyframe(jy.KeyframeProperty.uniform_scale, d_us, 1.06)
        script.add_segment(seg, "配图")
        n_img += 1

    S = round(min(W, H) * 0.058)
    portrait = H >= W
    y_sub = H - round(H * (0.17 if portrait else 0.07)) - S * 0.6
    y_pop = round(H * (0.27 if portrait else 0.16)) + S * 1.1
    y_title = round(H * (0.07 if portrait else 0.05)) + S * 0.6

    def trange(a, b):
        a_us = _us(a)
        return jy.Timerange(a_us, min(_us(b), total_us) - a_us)

    subs = [x for x in subs if _us(x["a"]) < total_us - 50_000]
    pops = [x for x in pops if _us(x["a"]) < total_us - 50_000]
    for s in subs:
        seg = jy.TextSegment(_clean(s["text"]), trange(s["a"], s["b"]),
                             style=jy.TextStyle(size=_size(S, H), bold=True, align=1),
                             border=jy.TextBorder(color=(0, 0, 0), width=40),
                             clip_settings=jy.ClipSettings(transform_y=_ty(y_sub, H)))
        script.add_segment(seg, "字幕")

    for p in pops:
        fx = p.get("effect", "pop")
        col = _rgb(HEX.get(p["color"], HEX["yellow"]))
        kw = dict(style=jy.TextStyle(size=_size(S * (1.7 if fx == "card" else 2.1), H), bold=True, align=1,
                                     color=(0.09, 0.09, 0.09) if fx == "card" and p["color"] == "yellow"
                                     else ((1, 1, 1) if fx == "card" else col)),
                  clip_settings=jy.ClipSettings(transform_y=_ty(y_pop, H)))
        if fx == "card":
            kw["background"] = jy.TextBackground(color=HEX.get(p["color"], HEX["yellow"]), round_radius=0.3,
                                                 height=0.3, width=0.3)
        else:
            kw["border"] = jy.TextBorder(color=(0, 0, 0), width=50)
        seg = jy.TextSegment(_clean(p["text"]), trange(p["a"], p["b"]), **kw)
        seg.add_animation({"pop": jy.TextIntro.弹入, "count": jy.TextIntro.放大,
                           "card": jy.TextIntro.翻动}.get(fx, jy.TextIntro.弹入), 0.3)
        script.add_segment(seg, "大字")

    title = plan.get("title") or ""
    if title:
        script.add_segment(jy.TextSegment(title, jy.Timerange(0, total_us),
                                          style=jy.TextStyle(size=_size(S * 1.1, H), bold=True, align=1),
                                          background=jy.TextBackground(color="#000000", alpha=0.47,
                                                                       round_radius=0.2),
                                          clip_settings=jy.ClipSettings(transform_y=_ty(y_title, H))), "标题")

    # 音效：剪映同一轨不能重叠 → 贪心分道
    placed, lanes = [], []
    for s in sorted(sfx, key=lambda x: x["t"]):
        f = SFX_DIR / s["file"]
        if not f.is_file():
            continue
        am = jy.AudioMaterial(str(f))
        start = _us(s["t"])
        if start >= total_us:
            continue
        dur = min(am.duration, total_us - start)
        lane = next((i for i, end in enumerate(lanes) if end <= start), len(lanes))
        if lane == len(lanes):
            lanes.append(0)
        lanes[lane] = start + dur
        placed.append((lane, start, dur, am, SFX_VOL.get(s["kind"], 0.5)))
    for i in range(len(lanes)):
        script.append_track(jy.TrackSpec(jy.TrackType.audio, name=f"音效{i + 1}"))
    for lane, start, dur, am, vol in placed:
        script.add_segment(jy.AudioSegment(am, jy.Timerange(start, dur), volume=vol), f"音效{lane + 1}")

    bgm = BGM_FILES.get(plan.get("bgm"))
    if bgm and (BGM_DIR / bgm).is_file():
        bm = jy.AudioMaterial(str(BGM_DIR / bgm))
        pos = 0
        while pos < total_us:              # BGM 不够长就接着铺
            d = min(bm.duration, total_us - pos)
            script.add_segment(jy.AudioSegment(bm, jy.Timerange(pos, d), source_timerange=jy.Timerange(0, d),
                                               volume=0.25), "BGM")
            pos += d
    script.save()
    _color_keywords(Path(staging) / name / "draft_content.json", subs)
    log(f"草稿已生成：{len(ranges)} 段视频、{n_img} 张配图、{len(subs)} 条字幕、{len(pops)} 个大字、{len(placed)} 个音效")
    return Path(staging) / name


def export(src, data, plan, out_dir, name, draft_root="", log=print, broll_dir=None):
    """生成草稿；在 Windows 上并且找得到剪映草稿目录就直接装进去。返回 (草稿路径, 是否已装进剪映)。"""
    staging = Path(out_dir) / "剪映草稿"
    staging.mkdir(parents=True, exist_ok=True)
    d = build_draft(src, data, plan, staging, name, log, broll_dir)
    if not sys.platform.startswith("win"):
        return str(d), False
    import windows_draft
    try:
        root = draft_root or windows_draft.default_draft_root()
    except FileNotFoundError:
        log("没找到剪映草稿目录（可在设置里填写剪映「全局设置-草稿位置」），草稿留在项目 out 文件夹里。")
        return str(d), False
    # 素材打包进草稿：原视频被移动/删除也不会报“媒体丢失”（会多占一份硬盘）
    target = windows_draft.install(str(d), name, root, bundle=True)
    log(f"已装进剪映草稿库：{target}")
    return target, True
