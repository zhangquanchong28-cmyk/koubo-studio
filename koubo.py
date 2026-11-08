# -*- coding: utf-8 -*-
"""云端剪辑命令行（给 Claude 在云端会话里用；本地用户用 start.bat 的网页工作台）。

项目目录格式和网页工作台完全一样（projects/<编号>/），所以云端做好的方案能打包带回本地，在原片上出高清。

  python koubo.py new 视频.mp4 [第2段.mp4 ...] [--name 名字]   导入（多段按顺序拼起来）并转写
  python koubo.py plan  <编号> [--notes "要求"]                  AI 出剪辑方案（会带上 经验/我的经验.md）
  python koubo.py show  <编号>                                    逐句列出方案（删/重点词/大字/配图…）
  python koubo.py broll <编号> [--ids 3,8] [--force] [--source ai|stock|auto]   找配图
  python koubo.py render <编号>                                   出片 → projects/<编号>/out/成片.mp4
  python koubo.py frames <编号> [--at 1.5,10,22]                 抽帧拼图，给 Claude 自查画面
  python koubo.py cover <编号> [--at 原片秒] [--text "封面文字"]    生成封面 out/封面.jpg
  python koubo.py jianying <编号>                                 生成剪映草稿目录
  python koubo.py pack  <编号>                                    打包「云端方案包.zip」→ 本地工作台导入后用原片出高清
  python koubo.py list                                            列出项目
改方案：直接改 projects/<编号>/plan.json，然后 python koubo.py check <编号> 校验。
"""
import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

import server  # noqa: E402  复用工作台的项目存取与任务函数
import planner  # noqa: E402
import packer  # noqa: E402
from common import find_tool  # noqa: E402


def cfg():
    c = server.load_cfg()
    if not (c.get("api_key") or "").strip():
        # 云端环境里 OpenRouter Key 由网络代理注入，代码里只放占位符
        c["api_key"] = "proxy-injected"
        c["provider"] = "openrouter"
    return c


def say(pid):
    return lambda m: (print(m, flush=True), server.log(pid, m))


def cmd_new(a):
    files = [Path(f).resolve() for f in a.files]
    for f in files:
        if not f.is_file():
            sys.exit(f"找不到文件：{f}")
    name = a.name or files[0].name
    pid = server.new_project(name)
    if len(files) == 1:
        src = files[0]
    else:  # 压缩代理切成了几段：按顺序无损拼回一个文件
        src = server.pdir(pid) / "source.mp4"
        lst = server.pdir(pid) / "parts.txt"
        lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
        subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(lst), "-c", "copy", str(src)], check=True)
    server.update(pid, source=str(src))
    print(f"项目编号：{pid}")
    c = cfg()
    server.update(pid, busy="Claude 转写", progress=0.0)
    try:
        data = server.asr.transcribe(src, c["whisper_model"], c["device"], log=say(pid),
                                     progress=lambda x: server.update(pid, progress=x))
    finally:
        server.update(pid, busy="")
    (server.pdir(pid) / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    server.update(pid, stage="transcribed")
    print(f"转写完成：{len(data['phrases'])} 句，{data['duration']:.0f} 秒")


def _load(pid):
    if not server.read_state(pid):
        sys.exit(f"没有这个项目：{pid}")
    return server.load_transcript(pid)


def cmd_plan(a):
    data = _load(a.pid)
    plan = planner.ai_plan(cfg(), data, a.notes or "", log=say(a.pid))
    server._write_plan(a.pid, plan)
    server.update(a.pid, stage="planned")
    print("思路：" + plan.get("notes", ""))
    cmd_show(a)


def cmd_show(a):
    data = _load(a.pid)
    plan = json.loads(server._plan_path(a.pid).read_text(encoding="utf-8"))
    emph = {e["id"]: e for e in plan.get("emphasis", [])}
    br = {b["id"]: b for b in plan.get("broll", [])}
    dele = set(plan.get("delete", []))
    print(f"标题：{plan.get('title')}  BGM：{plan.get('bgm')}  钩子：{plan.get('hook')}")
    for p in data["phrases"]:
        tags = []
        if p["id"] in dele or p.get("filler"):
            tags.append("删")
        e = emph.get(p["id"])
        if e:
            tags.append("重点:" + "/".join(e["keywords"]) + f"({e['color']})")
            if e["big"]:
                tags.append("大字:" + e.get("effect", "pop"))
            if e["zoom"]:
                tags.append("推近")
            if e["sfx"] != "none":
                tags.append("音效:" + e["sfx"])
        b = br.get(p["id"])
        if b:
            tags.append(f"配图:{b['layout']}「{b['desc']}」" + ("✓" if b.get("image") else "（未找图）"))
        if plan.get("hook") == p["id"]:
            tags.append("钩子")
        print(f"[{p['id']:>3}] {p['start']:6.1f}s {p['text']}" + (("  ← " + " | ".join(tags)) if tags else ""))


def cmd_check(a):
    data = _load(a.pid)
    raw = json.loads(server._plan_path(a.pid).read_text(encoding="utf-8"))
    plan = planner.sanitize(raw, data)
    if "usage" in raw:
        plan["usage"] = raw["usage"]
    server._write_plan(a.pid, plan)
    server.log(a.pid, "Claude 修改了方案")
    print("方案已校验保存。")


def cmd_broll(a):
    _load(a.pid)
    ids = [int(x) for x in a.ids.split(",")] if a.ids else None
    cost = server.fill_broll(a.pid, cfg(), ids, a.force, a.source)
    print(f"配图完成，本次约 ${cost:.3f}")


def cmd_render(a):
    data = _load(a.pid)
    c = cfg()
    if any(not b.get("image") for b in json.loads(server._plan_path(a.pid).read_text(encoding="utf-8")).get("broll", [])):
        server.fill_broll(a.pid, c)
    plan = json.loads(server._plan_path(a.pid).read_text(encoding="utf-8"))
    res = packer.render(server.source_of(a.pid), data, plan, server.pdir(a.pid) / "out", font=c["font"],
                        bgm_vol=float(c.get("bgm_vol", 0.10)), log=say(a.pid), broll_dir=server.pdir(a.pid) / "broll",
                        progress=lambda x: server.update(a.pid, progress=x))
    server.update(a.pid, stage="rendered", result=res, rendered_at=__import__("time").time())
    print(json.dumps(res, ensure_ascii=False))


def cmd_cover(a):
    data = _load(a.pid)
    plan = json.loads(server._plan_path(a.pid).read_text(encoding="utf-8"))
    t = a.at if a.at is not None else server.default_cover_time(data, plan)
    out = server.pdir(a.pid) / "out" / "封面.jpg"
    out.parent.mkdir(exist_ok=True)
    packer.make_cover(server.source_of(a.pid), t, a.text if a.text is not None else plan.get("title", ""),
                      plan, out, font=cfg()["font"])
    server.update(a.pid, cover={"at": __import__("time").time(), "t": t})
    print(f"封面：{out}")


def cmd_frames(a):
    out = server.pdir(a.pid) / "out"
    video = out / "成片.mp4"
    if not video.is_file():
        sys.exit("还没出片。")
    dur = packer.seg_duration(video)
    ts = [float(x) for x in a.at.split(",")] if a.at else [dur * (i + 0.5) / 8 for i in range(8)]
    ff = find_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        pics = []
        for i, t in enumerate(ts):
            f = Path(td) / f"{i}.png"
            subprocess.run([ff, "-loglevel", "error", "-y", "-ss", f"{min(t, dur - 0.05):.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=-2:480,drawtext=text='%.1fs':x=8:y=8:fontsize=22:"
                            "fontcolor=white:box=1:boxcolor=black@0.6" % t, str(f)], check=True)
            pics.append(f)
        sheet = out / "抽帧.png"
        cols = min(4, len(pics))
        rows = (len(pics) + cols - 1) // cols
        while len(pics) < rows * cols:  # 补空白格
            pics.append(pics[-1])
        args = sum([["-i", str(p)] for p in pics], [])
        # 每格同尺寸，用 xstack 按网格排
        w_expr = [f"{'+'.join(['w0'] * c) or '0'}_{'+'.join(['h0'] * r) or '0'}" for r in range(rows) for c in range(cols)]
        subprocess.run([ff, "-loglevel", "error", "-y"] + args + ["-filter_complex",
                        f"xstack=inputs={len(pics)}:layout={'|'.join(w_expr)}", str(sheet)], check=True)
    print(f"抽帧图：{sheet}")


def cmd_jianying(a):
    import jianying
    data = _load(a.pid)
    plan = json.loads(server._plan_path(a.pid).read_text(encoding="utf-8"))
    name = Path(server.read_state(a.pid).get("name", "口播")).stem[:30] + "_口播工作台"
    path, _ = jianying.export(server.source_of(a.pid), data, plan, server.pdir(a.pid) / "out", name,
                              log=say(a.pid), broll_dir=server.pdir(a.pid) / "broll")
    print(f"剪映草稿目录：{path}（素材路径是云端的，需要在本地重新导出才能用；本地用「导入云端方案包」更方便）")


def cmd_pack(a):
    _load(a.pid)
    d = server.pdir(a.pid)
    out = d / "out" / "云端方案包.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("koubo-pack.json", json.dumps({"version": 1, "name": server.read_state(a.pid).get("name")},
                                                 ensure_ascii=False))
        for n in ("transcript.json", "plan.json"):
            z.write(d / n, n)
        for f in sorted((d / "broll").glob("*")) if (d / "broll").is_dir() else []:
            z.write(f, "broll/" + f.name)
    print(f"方案包：{out}（{out.stat().st_size // 1024} KB）")


def cmd_list(a):
    for d in sorted(server.PROJECTS.glob("*"), reverse=True):
        st = server.read_state(d.name)
        if st:
            print(d.name, st.get("stage"), st.get("name"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new"); p.add_argument("files", nargs="+"); p.add_argument("--name")
    p = sub.add_parser("plan"); p.add_argument("pid"); p.add_argument("--notes")
    for n in ("show", "check", "render", "jianying", "pack"):
        sub.add_parser(n).add_argument("pid")
    p = sub.add_parser("broll"); p.add_argument("pid"); p.add_argument("--ids"); p.add_argument("--force", action="store_true")
    p.add_argument("--source", choices=["auto", "ai", "stock"])
    p = sub.add_parser("frames"); p.add_argument("pid"); p.add_argument("--at")
    p = sub.add_parser("cover"); p.add_argument("pid"); p.add_argument("--at", type=float)
    p.add_argument("--text", help="封面文字，用 \\n 换行；默认用标题")
    sub.add_parser("list")
    a = ap.parse_args()
    label = {"plan": "AI 出方案", "broll": "找配图", "render": "出片", "jianying": "导出剪映草稿",
             "check": "修改方案"}.get(a.cmd)
    pid = getattr(a, "pid", None)
    if label and pid and server.read_state(pid):
        # 让网页工作台（观看模式）实时显示 Claude 正在做什么
        server.update(pid, busy=f"Claude {label}", progress=0.0, error="")
        try:
            globals()["cmd_" + a.cmd](a)
        except BaseException as e:
            server.update(pid, error=str(e)[:300])
            raise
        finally:
            server.update(pid, busy="", progress=1.0)
    else:
        globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
