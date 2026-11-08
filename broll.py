# -*- coding: utf-8 -*-
"""配图：按方案里每句的描述找图。来源顺序（设置里可改）：
  stock = Pexels / Pixabay 免费可商用图库（需各自的免费 Key）
  ai    = OpenRouter 生图（用同一个 OpenRouter Key，按张计费）
只用标准库发请求，不增加依赖。"""
import base64
import json
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"
UA = "koubo-studio/3 (+local desktop tool)"


def aspect(layout, portrait):
    if layout == "full":
        return "9:16" if portrait else "16:9"
    if layout == "corner":
        return "1:1"
    return "4:3"


def _req(url, data=None, headers=None, timeout=120):
    h = {"User-Agent": UA}
    h.update(headers or {})
    body = json.dumps(data).encode("utf-8") if data is not None else None
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _save(folder, pid, raw, ext):
    folder.mkdir(parents=True, exist_ok=True)
    name = f"b{pid}_{int(time.time() * 1000) % 10_000_000}.{ext}"
    (folder / name).write_bytes(raw)
    return name


def _orient(layout, portrait):
    a = aspect(layout, portrait)
    return "portrait" if a == "9:16" else ("square" if a == "1:1" else "landscape")


# ---------------------------------------------------------------- 免费图库
def from_pexels(item, key, folder, portrait, used):
    text = item["query"] or item["desc"]
    args = {"query": text, "per_page": 15, "orientation": _orient(item["layout"], portrait)}
    if not text.isascii():
        args["locale"] = "zh-CN"
    q = urllib.parse.urlencode(args)
    res = json.loads(_req(f"https://api.pexels.com/v1/search?{q}", headers={"Authorization": key}))
    photos = [p for p in res.get("photos", []) if p["src"]["large2x"] not in used]
    if not photos:
        return None
    p = random.choice(photos[:6])
    url = p["src"]["large2x"]
    return _save(folder, item["id"], _req(url), "jpg"), url, f"Pexels · {p.get('photographer', '')}"


def from_pixabay(item, key, folder, portrait, used):
    o = _orient(item["layout"], portrait)
    text = item["query"] or item["desc"]
    q = urllib.parse.urlencode({"key": key, "q": text[:100], "image_type": "photo", "lang": "en" if text.isascii() else "zh",
                                "per_page": 20, "safesearch": "true",
                                "orientation": "vertical" if o == "portrait" else "horizontal"})
    res = json.loads(_req(f"https://pixabay.com/api/?{q}"))
    hits = [h for h in res.get("hits", []) if h["largeImageURL"] not in used]
    if not hits:
        return None
    h = random.choice(hits[:6])
    url = h["largeImageURL"]
    return _save(folder, item["id"], _req(url), "jpg"), url, f"Pixabay · {h.get('user', '')}"


# ---------------------------------------------------------------- AI 生图
def from_ai(item, cfg, folder, portrait):
    if cfg.get("provider", "openrouter") != "openrouter" or not (cfg.get("api_key") or "").strip():
        raise RuntimeError("AI 生图需要在设置里选 OpenRouter 并填 Key。")
    prompt = item["prompt"] or (item["desc"] + ", photorealistic, no text, no watermark")
    model = (cfg.get("image_model") or "").strip() or DEFAULT_IMAGE_MODEL
    try:
        raw = _req("https://openrouter.ai/api/v1/chat/completions", timeout=180,
                   headers={"Authorization": "Bearer " + cfg["api_key"].strip()},
                   data={"model": model, "modalities": ["image", "text"],
                         "image_config": {"aspect_ratio": aspect(item["layout"], portrait)},
                         "usage": {"include": True},
                         "messages": [{"role": "user", "content": prompt}]})
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"生图接口出错（{e.code}）：{msg}")
    res = json.loads(raw)
    if "error" in res:
        raise RuntimeError(f"生图失败：{res['error'].get('message', res['error'])}")
    msg = res["choices"][0]["message"]
    imgs = msg.get("images") or []
    if not imgs:
        raise RuntimeError("模型没有返回图片（可能这段描述被拒绝了），改一下描述再试。")
    m = re.match(r"data:image/(\w+);base64,(.*)", imgs[0]["image_url"]["url"], re.S)
    ext = {"jpeg": "jpg"}.get(m.group(1), m.group(1))
    cost = float((res.get("usage") or {}).get("cost") or 0)
    return _save(folder, item["id"], base64.b64decode(m.group(2)), ext), cost


def fetch(item, cfg, folder, portrait, source="auto", used=(), log=print, allow_ai=True):
    """给一条配图找图。返回 dict(image, source, credit, url, cost)。"""
    folder = Path(folder)
    used = set(used)
    if source in ("auto", "stock"):
        for name, key, fn in (("Pexels", cfg.get("pexels_key"), from_pexels),
                              ("Pixabay", cfg.get("pixabay_key"), from_pixabay)):
            if not (key or "").strip():
                continue
            try:
                r = fn(item, key.strip(), folder, portrait, used)
            except Exception as e:  # 图库出错就换下一个来源
                log(f"{name} 搜图失败：{e}")
                continue
            if r:
                return {"image": r[0], "source": "stock", "url": r[1], "credit": r[2], "cost": 0.0}
        if source == "stock":
            raise RuntimeError("免费图库没搜到（或没填图库 Key），可以改英文搜索词再试，或换 AI 生图。")
    if not allow_ai:
        raise RuntimeError("本次 AI 生图已到上限（设置里可改「每次最多 AI 生图张数」）。")
    name, cost = from_ai(item, cfg, folder, portrait)
    return {"image": name, "source": "ai", "url": "", "credit": "AI 生成", "cost": cost}
