# -*- coding: utf-8 -*-
"""本地网页工作台：只在本机运行（127.0.0.1），浏览器里完成 上传 → 转写 → AI 方案 → 调整 → 出片。"""
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

import asr  # noqa: E402
import broll  # noqa: E402
import packer  # noqa: E402
import planner  # noqa: E402

# Windows 注册表里的类型映射经常不对（.js 变成 text/plain），这里固定
for _t, _e in (("text/javascript", ".js"), ("text/html", ".html"), ("video/mp4", ".mp4"),
               ("video/quicktime", ".mov"), ("image/webp", ".webp"), ("text/plain", ".srt")):
    mimetypes.add_type(_t, _e)

PROJECTS = APP / "projects"
CONFIG = APP / "config.json"
DEFAULT_CFG = {"provider": "openrouter", "api_key": "", "model": "", "effort": "medium",
               "font": "Microsoft YaHei" if os.name == "nt" else "WenQuanYi Zen Hei",
               "whisper_model": "medium", "device": "cpu", "bgm_vol": 0.10, "jianying_root": "", "concat_path": "",
               "broll_source": "auto", "image_model": "", "pexels_key": "", "pixabay_key": "", "max_ai_images": 10}
SECRET_KEYS = ("api_key", "pexels_key", "pixabay_key")
LOCK = threading.Lock()


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if CONFIG.is_file():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    if os.environ.get("KOUBO_FOLLOW") == "1" and not (cfg.get("api_key") or "").strip():
        # 云端模式：OpenRouter Key 由网络代理注入，这里只放占位符
        cfg["api_key"], cfg["provider"] = "proxy-injected", "openrouter"
    return cfg


def save_cfg(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 项目状态
def pdir(pid):
    if not re.fullmatch(r"[0-9a-z_-]{1,64}", pid or ""):
        raise ValueError("bad project id")
    return PROJECTS / pid


def read_state(pid):
    f = pdir(pid) / "state.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def write_state(pid, st):
    with LOCK:
        f = pdir(pid) / "state.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)


def update(pid, **kw):
    st = read_state(pid) or {}
    st.update(kw)
    write_state(pid, st)
    return st


def log(pid, msg):
    st = read_state(pid) or {}
    st.setdefault("log", []).append(time.strftime("%H:%M:%S ") + msg)
    st["log"] = st["log"][-60:]
    write_state(pid, st)


def source_of(pid):
    st = read_state(pid)
    return Path(st["source"])


def run_job(pid, name, fn):
    st = read_state(pid)
    if st.get("busy"):
        raise RuntimeError("这个项目正在处理中，请稍等。")
    update(pid, busy=name, progress=0.0, error="")

    def work():
        try:
            fn()
        except Exception as e:  # 把错误显示到页面上
            traceback.print_exc()
            log(pid, f"出错：{e}")
            update(pid, error=str(e))
        finally:
            update(pid, busy="", progress=1.0)

    threading.Thread(target=work, daemon=True).start()


def job_transcribe(pid):
    cfg = load_cfg()

    def fn():
        data = asr.transcribe(source_of(pid), cfg["whisper_model"], cfg["device"],
                              log=lambda m: log(pid, m), progress=lambda x: update(pid, progress=x))
        (pdir(pid) / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        log(pid, f"转写完成：{len(data['phrases'])} 句。下一步点「AI 生成方案」。")
        update(pid, stage="transcribed")
    run_job(pid, "转写", fn)


def load_transcript(pid):
    f = pdir(pid) / "transcript.json"
    if not f.is_file():
        raise RuntimeError("还没转写完成。")
    return json.loads(f.read_text(encoding="utf-8"))


def job_plan(pid, notes):
    cfg = load_cfg()

    def fn():
        data = load_transcript(pid)
        plan = planner.ai_plan(cfg, data, notes, log=lambda m: log(pid, m))
        (pdir(pid) / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        log(pid, "方案已生成：" + (plan.get("notes") or ""))
        update(pid, stage="planned")
    run_job(pid, "AI 方案", fn)


def _plan_path(pid):
    return pdir(pid) / "plan.json"


def _write_plan(pid, plan):
    _plan_path(pid).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")


def fill_broll(pid, cfg, ids=None, force=False, source=None):
    """给方案里的配图找图（已有图的跳过，force=换一张）。返回本次花费。"""
    plan = json.loads(_plan_path(pid).read_text(encoding="utf-8"))
    items = [b for b in plan.get("broll", []) if (ids is None or b["id"] in ids) and (force or not b.get("image")
                                                   or not (pdir(pid) / "broll" / b["image"]).is_file())]
    if not items:
        return 0.0
    meta = packer.probe(source_of(pid))
    portrait = meta["h"] >= meta["w"]
    src = source or cfg.get("broll_source", "auto")
    limit = int(cfg.get("max_ai_images") or 10)
    total, ai_n = 0.0, 0
    used = set(read_state(pid).get("broll_used", []))
    for k, b in enumerate(items, 1):
        log(pid, f"配图 {k}/{len(items)}：{b['desc'] or b['query']}…")
        try:
            r = broll.fetch(b, cfg, pdir(pid) / "broll", portrait, src, used, log=lambda m: log(pid, m),
                            allow_ai=ai_n < limit)
        except Exception as e:
            log(pid, f"第 {b['id']} 句配图失败：{e}")
            continue
        old = b.get("image")
        b.update(image=r["image"], source=r["source"], credit=r["credit"])
        if r["url"]:
            used.add(r["url"])
        if r["source"] == "ai":
            ai_n += 1
        total += r["cost"]
        if old and old != r["image"]:
            (pdir(pid) / "broll" / old).unlink(missing_ok=True)
        update(pid, progress=k / len(items))
        # 每张图落盘一次，页面能边生成边看到
        cur = json.loads(_plan_path(pid).read_text(encoding="utf-8"))
        for cb in cur.get("broll", []):
            if cb["id"] == b["id"]:
                cb.update(image=b["image"], source=b["source"], credit=b["credit"])
        _write_plan(pid, cur)
    update(pid, broll_used=sorted(used)[-300:])
    if total:
        log(pid, f"AI 生图 {ai_n} 张，约 ${total:.3f}")
    return total


def job_chat(pid, text):
    cfg = load_cfg()

    def fn():
        data = load_transcript(pid)
        plan = json.loads(_plan_path(pid).read_text(encoding="utf-8"))
        new, reply, rule, usd = planner.revise_plan(cfg, data, plan, text, log=lambda m: log(pid, m))
        _write_plan(pid, new)
        if rule:
            planner.remember_rule(rule)
            reply += f"（已记住：{rule}）"
        hist = (read_state(pid).get("chat") or [])[-19:]
        hist.append({"q": text, "a": reply, "usd": usd, "at": time.time()})
        update(pid, chat=hist, stage="planned")
        log(pid, "AI：" + reply)
    run_job(pid, "AI 改方案", fn)


def job_broll(pid, ids=None, force=False, source=None):
    cfg = load_cfg()
    run_job(pid, "配图", lambda: fill_broll(pid, cfg, ids, force, source))


def job_render(pid):
    cfg = load_cfg()

    def fn():
        data = load_transcript(pid)
        if any(not b.get("image") for b in json.loads(_plan_path(pid).read_text(encoding="utf-8")).get("broll", [])):
            log(pid, "有配图还没准备，先自动找图…")
            fill_broll(pid, cfg)
        plan = json.loads(_plan_path(pid).read_text(encoding="utf-8"))
        res = packer.render(source_of(pid), data, plan, pdir(pid) / "out", font=cfg["font"],
                            bgm_vol=float(cfg.get("bgm_vol", 0.10)), log=lambda m: log(pid, m),
                            progress=lambda x: update(pid, progress=x), broll_dir=pdir(pid) / "broll")
        update(pid, stage="rendered", result=res, rendered_at=time.time())
    run_job(pid, "出片", fn)


def job_jianying(pid):
    cfg = load_cfg()

    def fn():
        import jianying  # 用到时才加载，没装 pyJianYingDraft 也不影响其他功能
        data = load_transcript(pid)
        plan = json.loads((pdir(pid) / "plan.json").read_text(encoding="utf-8"))
        st = read_state(pid)
        stem = re.sub(r'[\\/:*?"<>|.\s]+', "_", Path(st.get("name", "口播")).stem).strip("_") or "口播"
        name = f"{stem[:30]}_口播工作台"
        path, installed = jianying.export(source_of(pid), data, plan, pdir(pid) / "out", name,
                                          draft_root=cfg.get("jianying_root", ""), log=lambda m: log(pid, m),
                                          broll_dir=pdir(pid) / "broll")
        update(pid, jianying={"path": str(path), "installed": installed, "name": name, "at": time.time()})
        if installed:
            log(pid, f"打开剪映，草稿列表里找「{name}」（没看到就重启剪映）。")
    run_job(pid, "导出剪映草稿", fn)


def job_concat(pid):
    cfg = load_cfg()

    def fn():
        import concat_export
        data = load_transcript(pid)
        plan = json.loads((pdir(pid) / "plan.json").read_text(encoding="utf-8"))
        stem = re.sub(r'[\\/:*?"<>|.\s]+', "_", Path(read_state(pid).get("name", "口播")).stem).strip("_") or "口播"
        path = concat_export.export(source_of(pid), data, plan, pdir(pid) / "out", stem[:30],
                                    concat_path=cfg.get("concat_path", ""), log=lambda m: log(pid, m))
        update(pid, concat={"path": str(path), "at": time.time()})
    run_job(pid, "导出 Concat", fn)


def new_project(name):
    PROJECTS.mkdir(exist_ok=True)
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + str(int(time.time() * 1000) % 1000)
    pdir(pid).mkdir(parents=True)
    write_state(pid, {"id": pid, "name": name, "stage": "new", "busy": "", "log": [], "progress": 0.0})
    return pid


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, path, download_name=None):
        path = Path(path)
        if not path.is_file():
            return self._json({"error": "文件不存在"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = min(int(b) if a and b else size - 1, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if download_name:
            from urllib.parse import quote
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        try:
            u = urlparse(self.path)
            parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
            if not parts:
                return self._file(APP / "ui.html")
            if parts == ["timeline.js"]:
                return self._file(APP / "timeline.js")
            if parts == ["api", "config"]:
                cfg = load_cfg()
                cfg["has_key"] = bool(cfg.get("api_key"))
                cfg["follow"] = os.environ.get("KOUBO_FOLLOW") == "1"   # 云端观看模式：自动跟随 Claude 正在操作的项目
                for sk in SECRET_KEYS:
                    k = cfg.get(sk, "")
                    cfg[sk] = (k[:6] + "…" + k[-4:]) if len(k) > 12 else ("已填写" if k else "")
                return self._json(cfg)
            if parts == ["api", "projects"]:
                out = []
                if PROJECTS.is_dir():
                    for d in sorted(PROJECTS.iterdir(), reverse=True):
                        st = read_state(d.name) if d.is_dir() else None
                        if st:
                            o = {k: st.get(k) for k in ("id", "name", "stage", "busy")}
                            o["touched"] = (d / "state.json").stat().st_mtime
                            out.append(o)
                return self._json(out)
            if len(parts) == 3 and parts[:2] == ["api", "p"]:
                pid = parts[2]
                st = read_state(pid)
                if not st:
                    return self._json({"error": "没有这个项目"}, 404)
                d = pdir(pid)
                if (d / "transcript.json").is_file():
                    st["transcript"] = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
                    for p in st["transcript"]["phrases"]:
                        p.pop("words", None)
                if (d / "plan.json").is_file():
                    st["plan"] = json.loads((d / "plan.json").read_text(encoding="utf-8"))
                    st["plan_mtime"] = (d / "plan.json").stat().st_mtime
                return self._json(st)
            if len(parts) == 5 and parts[:2] == ["api", "p"] and parts[3] == "file":
                pid, name = parts[2], parts[4]
                if name == "source":
                    return self._file(source_of(pid))
                if name.startswith("broll:"):   # 配图预览
                    img = name[6:]
                    if not planner.IMG_NAME.match(img):
                        return self._json({"error": "不允许"}, 403)
                    return self._file(pdir(pid) / "broll" / img)
                if name not in ("成片.mp4", "字幕.srt", "包装字幕.ass", "方案.json"):
                    return self._json({"error": "不允许"}, 403)
                dl = parse_qs(u.query).get("dl")
                st = read_state(pid)
                base = Path(st["name"]).stem
                return self._file(pdir(pid) / "out" / name, f"{base}_{name}" if dl else None)
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            u = urlparse(self.path)
            parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
            if parts == ["api", "config"]:
                body = self._body()
                cfg = load_cfg()
                for k in DEFAULT_CFG:
                    if k in body and body[k] is not None:
                        if k in SECRET_KEYS and ("…" in str(body[k]) or body[k] == "已填写"):
                            continue  # 页面显示的是打码值，没改就不覆盖
                        cfg[k] = body[k]
                save_cfg(cfg)
                return self._json({"ok": True})
            if parts == ["api", "upload"]:
                name = parse_qs(u.query).get("name", ["视频.mp4"])[0]
                name = Path(name).name or "视频.mp4"
                pid = new_project(name)
                dst = pdir(pid) / ("source" + (Path(name).suffix.lower() or ".mp4"))
                left = int(self.headers.get("Content-Length") or 0)
                with open(dst, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                update(pid, source=str(dst))
                log(pid, f"已导入 {name}")
                job_transcribe(pid)
                return self._json({"id": pid})
            if parts == ["api", "import"]:      # 云端方案包 + 本地原片 → 新项目，直接能出高清
                import zipfile
                body = self._body()
                video = Path(str(body.get("video", "")).strip().strip('"'))
                pack = Path(str(body.get("pack", "")).strip().strip('"'))
                if not video.is_file():
                    return self._json({"error": f"找不到原片：{video}"}, 400)
                if not pack.is_file():
                    return self._json({"error": f"找不到方案包：{pack}"}, 400)
                with zipfile.ZipFile(pack) as z:
                    names = set(z.namelist())
                    if not {"transcript.json", "plan.json"} <= names:
                        return self._json({"error": "这不是口播工作台的云端方案包"}, 400)
                    data = json.loads(z.read("transcript.json").decode("utf-8"))
                    meta = packer.probe(video)
                    if abs(meta["duration"] - float(data["duration"])) > 3:
                        return self._json({"error": f"原片时长 {meta['duration']:.0f} 秒和方案里的 "
                                                    f"{float(data['duration']):.0f} 秒对不上，是不是选错视频了？"}, 400)
                    pid = new_project(video.name)
                    d = pdir(pid)
                    (d / "transcript.json").write_bytes(z.read("transcript.json"))
                    (d / "plan.json").write_bytes(z.read("plan.json"))
                    for n in names:
                        img = n[len("broll/"):] if n.startswith("broll/") else ""
                        if img and planner.IMG_NAME.match(img):
                            (d / "broll").mkdir(exist_ok=True)
                            (d / "broll" / img).write_bytes(z.read(n))
                update(pid, source=str(video), stage="planned")
                log(pid, f"已导入云端方案（原片：{video}）。检查一下方案，点「出片」用原片出高清。")
                return self._json({"id": pid})
            if parts == ["api", "open"]:
                path = Path(self._body().get("path", "").strip().strip('"'))
                if not path.is_file():
                    return self._json({"error": f"找不到文件：{path}"}, 400)
                pid = new_project(path.name)
                update(pid, source=str(path))
                log(pid, f"已打开 {path}（直接读取原文件，不复制）")
                job_transcribe(pid)
                return self._json({"id": pid})
            if len(parts) == 4 and parts[:2] == ["api", "p"]:
                pid, act = parts[2], parts[3]
                if not read_state(pid):
                    return self._json({"error": "没有这个项目"}, 404)
                if act == "brollup":            # 上传自己的配图：?bid=句子编号&name=文件名，正文是图片
                    q = parse_qs(u.query)
                    bid = int(q.get("bid", ["-1"])[0])
                    ext = Path(q.get("name", ["x.jpg"])[0]).suffix.lower().lstrip(".")
                    if ext not in ("jpg", "jpeg", "png", "webp"):
                        return self._json({"error": "只支持 jpg / png / webp 图片"}, 400)
                    n = int(self.headers.get("Content-Length") or 0)
                    if not 0 < n <= 30 << 20:
                        return self._json({"error": "图片太大（最大 30MB）"}, 400)
                    raw = self.rfile.read(n)
                    plan = json.loads(_plan_path(pid).read_text(encoding="utf-8"))
                    b = next((b for b in plan.get("broll", []) if b["id"] == bid), None)
                    if not b:
                        return self._json({"error": "先在这句选一个配图方式并保存方案"}, 400)
                    fname = broll._save(pdir(pid) / "broll", bid, raw, "jpg" if ext == "jpeg" else ext)
                    if b.get("image") and planner.IMG_NAME.match(b["image"]):
                        (pdir(pid) / "broll" / b["image"]).unlink(missing_ok=True)
                    b.update(image=fname, source="own", credit="自己的图")
                    _write_plan(pid, plan)
                    return self._json({"ok": True, "plan": plan})
                body = self._body()
                if act == "transcribe":
                    job_transcribe(pid)
                elif act == "plan":
                    job_plan(pid, body.get("notes", ""))
                elif act == "save":
                    data = load_transcript(pid)
                    plan = planner.sanitize(body.get("plan", {}), data)
                    old = pdir(pid) / "plan.json"
                    if old.is_file():  # 保留 AI 用量记录
                        prev = json.loads(old.read_text(encoding="utf-8"))
                        if "usage" in prev:
                            plan["usage"] = prev["usage"]
                    old.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
                    update(pid, stage="planned")
                    return self._json({"ok": True, "plan": plan})
                elif act == "render":
                    job_render(pid)
                elif act == "jianying":
                    if not (pdir(pid) / "plan.json").is_file():
                        return self._json({"error": "先生成或保存方案"}, 400)
                    job_jianying(pid)
                elif act == "chat":
                    text = str(body.get("text", "")).strip()
                    if not text:
                        return self._json({"error": "说点什么再发送"}, 400)
                    if not _plan_path(pid).is_file():
                        return self._json({"error": "先生成方案，再用一句话修改"}, 400)
                    job_chat(pid, text[:500])
                elif act == "broll":
                    if not _plan_path(pid).is_file():
                        return self._json({"error": "先生成或保存方案"}, 400)
                    ids = body.get("ids")
                    job_broll(pid, [int(i) for i in ids] if ids else None, bool(body.get("force")),
                              body.get("source") if body.get("source") in ("auto", "ai", "stock") else None)
                elif act == "concat":
                    if not (pdir(pid) / "plan.json").is_file():
                        return self._json({"error": "先生成或保存方案"}, 400)
                    job_concat(pid)
                elif act == "delete":
                    if read_state(pid).get("busy"):
                        return self._json({"error": "处理中，不能删除"}, 400)
                    shutil.rmtree(pdir(pid), ignore_errors=True)
                else:
                    return self._json({"error": "unknown action"}, 404)
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def main():
    PROJECTS.mkdir(exist_ok=True)
    port = int(os.environ.get("KOUBO_PORT", "8765"))
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer((os.environ.get("KOUBO_HOST", "127.0.0.1"), p), H)
            break
        except OSError:
            continue
    else:
        sys.exit("找不到可用端口")
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"口播工作台已启动：{url}\n（关闭这个黑色窗口就会停止）")
    if os.environ.get("KOUBO_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
