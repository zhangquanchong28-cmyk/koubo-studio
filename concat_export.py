# -*- coding: utf-8 -*-
"""【实验】导出到 Concat（开源剪辑器，github.com/jub0t/Concat）。

通过 Concat 自带的 MCP 接口（`concat --mcp headless`，标准输入输出上的 JSON-RPC）
在 Concat 里搭出工程：原片按方案切好、推镜头关键帧、字幕（重点词变色）、大字、顶部标题、音效、BGM。
导完用 Concat 打开 projects\\项目编号\\out\\Concat\\<工程名> 继续精修/导出。

⚠️ 这一部分没有在真机上跑过（云端无法编译 Concat），接口按 Concat 源码里的工具定义写的。
   不支持：开头钩子（Concat 里请手动复制那一段到最前），数字滚动/3D卡片在 Concat 里退化为普通大字/色卡。
"""
import json
import os
import shutil
import subprocess
import threading
import time

import settings as SET
from pathlib import Path

from packer import (BGM_DIR, BGM_FILES, SFX_DIR, SFX_VOL, ZOOM_IN_SEC, ZOOM_TO,
                    _clean, build_events, build_timeline, probe)

HEX = {"yellow": "#ffd400", "red": "#ff3b30", "blue": "#3ea6ff"}


def find_concat(cfg_path=""):
    cands = [cfg_path] if cfg_path else []
    cands += [shutil.which("concat") or "", shutil.which("concat-mcp") or ""]
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        if base:
            cands += [os.path.join(base, "Programs", "Concat", "concat.exe"), os.path.join(base, "Concat", "concat.exe")]
    for c in cands:
        if c and Path(c).is_file():
            return c
    raise RuntimeError("没找到 Concat。请先安装 Concat，并在设置里填 concat.exe 的完整路径。")


class MCP:
    """最小 MCP 客户端：一行一条 JSON-RPC。"""

    def __init__(self, exe, roots):
        args = [exe] if Path(exe).stem.lower() == "concat-mcp" else [exe, "--mcp"]
        env = dict(os.environ, CONCAT_MCP_ROOTS=os.pathsep.join(str(r) for r in roots))
        self.p = subprocess.Popen(args + ["headless"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8", bufsize=1)
        self.err = []
        threading.Thread(target=lambda: [self.err.append(x) for x in self.p.stderr], daemon=True).start()
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "koubo-studio", "version": "2"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("Concat 意外退出：" + "".join(self.err[-5:]))
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"Concat {method} 出错：{msg['error'].get('message')}")
                return msg["result"]

    def tool(self, tool_name, /, **args):
        name = tool_name
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        if r.get("isError"):
            txt = " ".join(c.get("text", "") for c in r.get("content", []))
            raise RuntimeError(f"Concat {name} 被拒：{txt[:300]}")
        if "structuredContent" in r:
            return r["structuredContent"]
        try:
            return json.loads(r["content"][0]["text"])
        except (KeyError, IndexError, ValueError):
            return {}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def export(src, data, plan, out_dir, name, concat_path="", log=print):
    exe = find_concat(concat_path)
    if any(b.get("image") for b in plan.get("broll", [])):
        log("提示：配图暂时不会带到 Concat，需要的话在 Concat 里手动加（图片在项目的 broll 文件夹里）。")
    meta = probe(src)
    W, H = meta["w"], meta["h"]
    if SET.of(plan) != SET.normalize({}):
        log("提示：全片设置（变速、比例、调色、字幕样式等）暂时不会带到 Concat。")
    plan = dict(plan, hook=None, settings={})        # 钩子、全片设置暂不支持
    ranges = build_timeline(data, plan)
    if not ranges:
        raise RuntimeError("方案里没有要保留的内容。")
    for a, b in zip(ranges, ranges[1:]):         # 相邻段可能重叠几十毫秒，切点必须单调
        b["start"] = max(b["start"], a["end"])
    ranges = [r for r in ranges if r["end"] - r["start"] > 0.05]
    durs = [r["end"] - r["start"] for r in ranges]
    subs, pops, sfx, total = build_events(ranges, durs, plan)

    loc = Path(out_dir) / "Concat"
    loc.mkdir(parents=True, exist_ok=True)
    pname = name if not (loc / name).exists() else f"{name}_{time.strftime('%H%M%S')}"
    path = str(loc / pname)
    num, den = (meta["fps"].split("/") + ["1"])[:2] if "/" in meta["fps"] else (meta["fps"], "1")
    m = MCP(exe, [loc, Path(src).parent, SFX_DIR.parent])
    try:
        log("Concat：新建工程…")
        m.tool("project_create", location=str(loc), name=pname,
               video={"width": W, "height": H, "rateNum": int(float(num)), "rateDen": int(float(den))})
        media = m.tool("media_import", path=path, file=str(Path(src).resolve()))["createdId"]
        tl = m.tool("timeline_read", path=path)["timeline"]
        main = tl["tracks"][-1]["id"] if tl.get("tracks") else m.tool("track_add", path=path, bottom=True)["createdId"]
        clip = m.tool("clip_add", path=path, mediaId=media, trackId=main, start=0.0)["createdId"]

        # 切：在每个保留段的边界切开，再把不要的段波纹删除
        log(f"Concat：切 {len(ranges)} 段…")
        ids = [clip]
        bounds = sorted({round(t, 4) for r in ranges for t in (r["start"], r["end"]) if 0.01 < t < meta["duration"] - 0.01})
        for t in bounds:
            r = m.tool("clips_split", path=path, clipIds=ids, time=t)
            if r.get("createdId"):
                ids.append(r["createdId"])
        clips = [c for c in m.tool("timeline_read", path=path)["timeline"]["clips"] if c.get("media") == media]
        drop = [c["id"] for c in clips
                if not any(r["start"] < (c["start"] + c["end"]) / 2 < r["end"] for r in ranges)]
        if drop:
            m.tool("clips_remove", path=path, clipIds=drop, ripple=True)

        # 推镜头：按源起点找到对应片段
        clips = [c for c in m.tool("timeline_read", path=path)["timeline"]["clips"] if c.get("media") == media]
        for r in ranges:
            if not r["zoom"]:
                continue
            c = next((c for c in clips if abs(c.get("sourceStart", -1) - r["start"]) < 0.02), None)
            if c and c["duration"] > 0:
                m.tool("keyframe_set", path=path, clipId=c["id"], property="scale", at=0.0, value=1.0)
                m.tool("keyframe_set", path=path, clipId=c["id"], property="scale",
                       at=min(1.0, ZOOM_IN_SEC / c["duration"]), value=ZOOM_TO, ease=[0.25, 0.1, 0.25, 1.0])

        S = min(W, H) * 0.058
        log(f"Concat：字幕 {len(subs)} 条、大字 {len(pops)} 个…")
        m.tool("captions_add", path=path, offsetY=0.33 if H >= W else 0.36,
               captions=[{"start": s["a"], "end": s["b"], "text": _clean(s["text"])} for s in subs],
               style={"fontSize": round(S / H, 4), "fontWeight": 800, "color": "#ffffff"})
        texts = [c for c in m.tool("timeline_read", path=path)["timeline"]["clips"] if c.get("kind") == "text"]
        for s in subs:
            if not s["kws"]:
                continue
            t = _clean(s["text"])
            c = next((c for c in texts if c.get("text") == t and abs(c["start"] - s["a"]) < 0.05), None)
            if not c:
                continue
            for kw, _ in s["kws"]:
                k = _clean(kw)
                i = t.find(k)
                if k and i >= 0:
                    m.tool("title_style_words", path=path, clipId=c["id"], start=i, end=i + len(k),
                           patch={"color": HEX.get(s["color"], HEX["yellow"]), "fontWeight": 900})

        for p in pops:
            card = p.get("effect") == "card"
            style = {"content": _clean(p["text"]), "fontSize": round(S * (1.7 if card else 2.1) / H, 4),
                     "fontWeight": 900, "color": "#181818" if card and p["color"] == "yellow" else
                     ("#ffffff" if card else HEX.get(p["color"], HEX["yellow"]))}
            if card:
                style["background"] = HEX.get(p["color"], HEX["yellow"]) + "ff"
            cid = m.tool("title_add", path=path, start=p["a"], duration=max(0.3, p["b"] - p["a"]), above=True,
                         style=style, offsetY=-0.22 if H >= W else -0.28)["createdId"]
            d = max(0.3, p["b"] - p["a"])
            for at, v in ((0.0, 0.35), (min(0.16 / d, 0.4), 1.18), (min(0.3 / d, 0.6), 1.0)):
                m.tool("keyframe_set", path=path, clipId=cid, property="scale", at=at, value=v)

        if plan.get("title"):
            m.tool("title_add", path=path, start=0.0, duration=total, above=True, offsetY=-0.42,
                   style={"content": plan["title"], "fontSize": round(S * 1.1 / H, 4), "fontWeight": 800,
                          "color": "#ffffff", "background": "#00000078"})

        # 音效：每个音效放一条不重叠的音轨上
        lanes, mids = [], {}
        for s in sorted(sfx, key=lambda x: x["t"]):
            f = SFX_DIR / s["file"]
            if not f.is_file() or s["t"] >= total:
                continue
            if s["file"] not in mids:
                mids[s["file"]] = m.tool("media_import", path=path, file=str(f.resolve()))["createdId"]
            lane = next((i for i, (_, end) in enumerate(lanes) if end <= s["t"]), None)
            if lane is None:
                lanes.append([m.tool("track_add", path=path, bottom=True)["createdId"], 0.0])
                lane = len(lanes) - 1
            r = m.tool("clip_add", path=path, mediaId=mids[s["file"]], trackId=lanes[lane][0], start=s["t"])
            cid = r["createdId"]
            c = next((c for c in r.get("timeline", {}).get("clips", []) if c["id"] == cid), None)
            lanes[lane][1] = c["end"] if c else s["t"] + 2.0
            m.tool("clip_update", path=path, clipId=cid, patch={"volume": SFX_VOL.get(s["kind"], 0.5)})

        bgm = BGM_FILES.get(plan.get("bgm"))
        if bgm and (BGM_DIR / bgm).is_file():
            bid = m.tool("media_import", path=path, file=str((BGM_DIR / bgm).resolve()))["createdId"]
            track = m.tool("track_add", path=path, bottom=True)["createdId"]
            pos = 0.0
            while pos < total - 0.05:
                r = m.tool("clip_add", path=path, mediaId=bid, trackId=track, start=pos)
                cid = r["createdId"]
                c = next((c for c in r.get("timeline", {}).get("clips", []) if c["id"] == cid), None)
                end = c["end"] if c else total
                if end > total:
                    m.tool("clip_trim", path=path, clipId=cid, edge="end", delta=-(end - total))
                    end = total
                m.tool("clip_update", path=path, clipId=cid, patch={"volume": 0.25, "fadeOut": 2.0 if end >= total else 0})
                if end <= pos:
                    break
                pos = end
        m.tool("project_save", path=path)
        log(f"Concat 工程已保存：{path}（用 Concat 打开它继续精修或导出）")
        return path
    finally:
        m.close()
