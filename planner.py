# -*- coding: utf-8 -*-
"""剪辑方案：调用 Claude（OpenRouter 或 Anthropic 直连）按口播方法论决定删哪些句、怎么包装。
没有 API Key 时退回到本地规则方案（只去语气词、给数字做强调）。"""
import json
import re
from pathlib import Path

import anthropic

SFX_KINDS = ["whoosh", "hit", "ding", "pop", "type", "page", "sparkle", "tick", "none"]
COLORS = ["yellow", "red", "blue"]
BGM_KINDS = ["upbeat", "chill", "hiphop", "none"]
EFFECTS = ["pop", "count", "card"]
LAYOUTS = ["pip", "full", "corner"]
IMG_NAME = re.compile(r"^[\w\-]{1,60}\.(?:jpg|jpeg|png|webp)$")

SYSTEM_PROMPT = """你是一名资深短视频口播剪辑师，报价 200～300 元/分钟。你只负责做"剪辑方案"，由程序执行。

## 剪辑原则（必须遵守）
1. 删除：口误、说错后重说的前一遍（保留最后说对的那一遍）、重复啰嗦、无意义语气词、明显跑题、开拍前后的准备话。保留内容要连贯、意思完整，不改变顺序。
2. 开头钩子：前 3 秒决定流量。如果全片中有比开头更抓人的一句（结论、冲突、反常识、数字），把它的编号填到 hook，程序会把这句复制一份放到最开头。开头已经足够抓人就填 null。
3. 重点包装（emphasis）：
   - 不要句句都做，大约每 2～3 句保留句做 1 句，开头前 2 句可以更密。
   - keywords：该句里最该被记住的 1～2 个词（数字、结论、反转词、核心概念），必须是原句里一字不差的连续片段，2～8 个字。
   - color：yellow=普通重点，red=警示/负面/风险，blue=科技/数据感。全片以 yellow 为主。
   - big=true 表示把关键词放大到画面中上方单独弹出（视觉冲击最强），全片约每 4～6 句用一次，开头第一句一般用。
   - effect（只在 big=true 时生效，决定大字怎么出现）：pop=放大弹出（默认）；count=数字滚动，从 0 滚到目标值，只用于关键词含阿拉伯数字的句子（如"3000元""95%"）；card=3D 翻转色卡，适合核心概念、结论、反转词。全片 count 和 card 各不超过 3～4 次，别连续两次用同一种。
   - zoom=true 表示这一句镜头推近，约每 3～4 句一次，用在情绪/重点句，不要连续两句都推。
   - sfx：配合画面的音效。whoosh=转场/切换，hit=重音强调/震撼，ding=提示/结论，pop=弹出/轻快，type=打字/列举，page=翻页/下一点，sparkle=惊喜/美好，tick=时间/数字计数，none=不加。同一种音效不要连用超过 3 次。
4. title：全片顶部常驻标题，≤14 个字，概括视频核心卖点（像晨晨的"留学生进国央企"）；不适合就留空字符串。
5. 配图（broll）：口播一直是人脸会单调，在适合的句子上配一张图，人声继续。
   - 选句子：说到具体的东西/场景/人群（"打开手机""在写字楼加班""爸妈催婚"）、举例子、讲概念时最适合；纯观点、情绪句、开头第一句、钩子句不配。
   - 数量：大约每 5～8 句配 1 张，3 分钟视频 5～8 张，不要连续两句都配；配图的句子不要再用 big 大字。
   - desc：中文一句话说明画面（给用户看）。
   - query：2～4 个英文单词，用来在免费图库搜实拍照片（如 "office night overtime"）。
   - prompt：英文生图提示词，写实摄影风格，描述主体、场景、光线、构图，结尾加 "photorealistic, no text, no watermark"。不要出现真实名人、品牌 logo、文字。
   - layout：pip=画中画（图片在画面上半部分，人物保留在下方，竖屏默认用这个）；full=全屏切到图片（重要场景，全片最多 2 次）；corner=右上角小窗（轻量补充）。
6. bgm：upbeat=轻快活泼，chill=舒缓治愈/情感，hiphop=有态度/节奏感，none=严肃话题不要音乐。

## 输出
只输出一个 JSON 对象，不要任何其他文字：
{"title": "...", "hook": 编号或null, "delete": [编号...],
 "emphasis": [{"id": 编号, "keywords": ["..."], "color": "yellow", "big": false, "effect": "pop", "zoom": false, "sfx": "pop"}],
 "broll": [{"id": 编号, "desc": "...", "query": "...", "prompt": "...", "layout": "pip"}],
 "bgm": "upbeat", "notes": "一句话说明你的剪辑思路"}"""


EXP_FILE = Path(__file__).resolve().parent / "经验" / "我的经验.md"


def user_rules():
    """读「经验/我的经验.md」里以 - 开头的条目（按小节分组），拼进系统提示词。"""
    if not EXP_FILE.is_file():
        return ""
    out, head = [], ""
    for line in EXP_FILE.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t.startswith("## "):
            head = t[3:].strip()
        elif t.startswith("- ") and len(t) > 3:
            out.append(f"- [{head}] {t[2:].strip()}" if head else t)
    if not out:
        return ""
    return ("\n\n## 用户自己的剪辑经验（优先级最高，和上面冲突时以这里为准）\n" + "\n".join(out[:200]))


def system_prompt():
    return SYSTEM_PROMPT + user_rules()


def make_client(cfg):
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return None
    if cfg.get("provider", "openrouter") == "openrouter":
        # OpenRouter 提供 Anthropic 原生 Messages 接口，官方 SDK 只需换 base_url，用 Bearer 认证
        return anthropic.Anthropic(base_url="https://openrouter.ai/api", auth_token=key, api_key=None,
                                   max_retries=2, timeout=600)
    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=600)


def model_id(cfg):
    m = (cfg.get("model") or "").strip()
    if m:
        return m
    return "anthropic/claude-opus-5.5" if cfg.get("provider", "openrouter") == "openrouter" else "claude-opus-5-5"


def phrases_text(phrases):
    lines = []
    for p in phrases:
        tag = "（语气词）" if p.get("filler") else ""
        lines.append(f"[{p['id']}] {p['start']:.1f}s 时长{p['end'] - p['start']:.1f}s：{p['text']}{tag}")
    return "\n".join(lines)


def _call(client, cfg, user_text, log):
    kwargs = dict(
        model=model_id(cfg), max_tokens=16000, system=system_prompt(),
        thinking={"type": "adaptive"}, output_config={"effort": cfg.get("effort", "medium")},
        messages=[{"role": "user", "content": user_text}],
    )
    try:
        return client.messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        # 少数中转/旧模型不认 thinking / output_config，去掉再试一次
        log(f"请求参数被拒（{e.message[:120]}），改用基础参数重试…")
        kwargs.pop("thinking", None)
        kwargs.pop("output_config", None)
        return client.messages.create(**kwargs)


def ai_plan(cfg, data, notes="", log=print):
    client = make_client(cfg)
    if client is None:
        log("没有填 API Key，使用本地规则方案。")
        return rule_plan(data)
    user_text = (f"视频总时长 {data['duration']:.0f} 秒。\n"
                 f"用户要求：{notes or '无特别要求，按口播精品标准剪'}\n\n"
                 f"以下是转写短句（编号、开始时间、时长、文本）：\n{phrases_text(data['phrases'])}")
    log(f"正在请 {model_id(cfg)} 生成剪辑方案…")
    try:
        resp = _call(client, cfg, user_text, log)
    except anthropic.AuthenticationError:
        raise RuntimeError("API Key 无效，请在设置里检查。")
    except anthropic.PermissionDeniedError:
        raise RuntimeError("API Key 没有权限，或账户余额不足。")
    except anthropic.NotFoundError:
        raise RuntimeError(f"找不到模型 {model_id(cfg)}，请在设置里检查模型名。")
    except anthropic.RateLimitError:
        raise RuntimeError("请求太频繁或额度用完，稍后再试。")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API 出错（{e.status_code}）：{e.message[:200]}")
    except anthropic.APIConnectionError:
        raise RuntimeError("连不上 API，请检查网络。")
    if resp.stop_reason == "refusal":
        raise RuntimeError("模型拒绝处理这段内容，请检查文案或换个模型。")
    text = "".join(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    cost = estimate_cost(model_id(cfg), u.input_tokens, u.output_tokens)
    log(f"用量：输入 {u.input_tokens} / 输出 {u.output_tokens} tokens，约 ${cost:.3f}")
    plan = parse_plan(text, data)
    plan["usage"] = {"input": u.input_tokens, "output": u.output_tokens, "usd": round(cost, 4)}
    return plan


PRICES = {"opus-5.5": (4, 20), "opus-5-5": (4, 20), "sonnet-5.5": (2, 10), "sonnet-5-5": (2, 10),
          "haiku-5.5": (0.1, 0.5), "haiku-5-5": (0.1, 0.5)}


def estimate_cost(model, tin, tout):
    for k, (pi, po) in PRICES.items():
        if k in model:
            return tin / 1e6 * pi + tout / 1e6 * po
    return tin / 1e6 * 4 + tout / 1e6 * 20


def _json_from(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("模型没有返回 JSON 方案，请重试。")
    return json.loads(m.group(0))


def parse_plan(text, data):
    raw = _json_from(text)
    return sanitize(raw, data)


def _effect(fx, kws):
    fx = fx if fx in EFFECTS else "pop"
    if fx == "count" and not (kws and re.search(r"\d", kws[0])):
        fx = "pop"          # 数字滚动只对阿拉伯数字有效
    return fx


def sanitize(raw, data):
    """把模型/用户给的方案校正到程序能执行的范围。"""
    by_id = {p["id"]: p for p in data["phrases"]}

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    delete = sorted({_int(i) for i in raw.get("delete", []) if _int(i) in by_id})
    hook = _int(raw.get("hook"))
    if hook not in by_id or hook in delete:
        hook = None
    emph = []
    seen = set()
    for e in raw.get("emphasis", []):
        try:
            pid = int(e.get("id"))
        except (TypeError, ValueError):
            continue
        if pid not in by_id or pid in delete or pid in seen:
            continue
        seen.add(pid)
        kws = [k for k in e.get("keywords", []) if isinstance(k, str) and k.strip() and k.strip() in by_id[pid]["text"]][:2]
        emph.append({
            "id": pid, "keywords": [k.strip() for k in kws],
            "color": e.get("color") if e.get("color") in COLORS else "yellow",
            "big": bool(e.get("big")) and bool(kws),
            "effect": _effect(e.get("effect"), kws),
            "zoom": bool(e.get("zoom")),
            "sfx": e.get("sfx") if e.get("sfx") in SFX_KINDS else "none",
        })
    broll, bseen = [], set()
    for b in raw.get("broll", []) or []:
        pid = _int(b.get("id")) if isinstance(b, dict) else None
        if pid not in by_id or pid in delete or pid in bseen:
            continue
        bseen.add(pid)
        img = str(b.get("image") or "")
        broll.append({
            "id": pid, "desc": str(b.get("desc") or "")[:80], "query": str(b.get("query") or "")[:80],
            "prompt": str(b.get("prompt") or "")[:600],
            "layout": b.get("layout") if b.get("layout") in LAYOUTS else "pip",
            "image": img if IMG_NAME.match(img) else "",
            "source": str(b.get("source") or "")[:10],
            "credit": str(b.get("credit") or "")[:60],
        })
    for e in emph:                       # 配图的句子不再弹大字，避免画面打架
        if e["id"] in bseen:
            e["big"] = False
    return {
        "title": str(raw.get("title") or "")[:20],
        "hook": hook, "delete": delete,
        "emphasis": sorted(emph, key=lambda x: x["id"]),
        "broll": sorted(broll, key=lambda x: x["id"]),
        "bgm": raw.get("bgm") if raw.get("bgm") in BGM_KINDS else "upbeat",
        "notes": str(raw.get("notes") or ""),
    }


NUM = re.compile(r"[0-9][0-9.]*[%％倍个万亿元块岁年天分秒条]?|[一二三四五六七八九十百千万亿两]{1,4}[%％倍个万亿元块岁年天条]")


def rule_plan(data):
    """不调用 AI 的保底方案：删语气词，每 3 句给一个数字/句尾词做强调。"""
    delete = [p["id"] for p in data["phrases"] if p.get("filler")]
    emph, n = [], 0
    for p in data["phrases"]:
        if p["id"] in delete:
            continue
        n += 1
        m = NUM.search(p["text"])
        if n == 1 or (n % 3 == 0 and m):
            kw = m.group(0) if m else p["text"].strip("，。！？,.!? ")[-4:]
            big = n == 1 or (m is not None and n % 6 == 3)
            fx = "count" if (m and big and re.search(r"\d", kw)) else "pop"
            emph.append({"id": p["id"], "keywords": [kw], "color": "yellow", "big": big, "effect": fx,
                         "zoom": n % 6 == 0, "sfx": "pop" if n > 1 else "hit"})
    return sanitize({"title": "", "hook": None, "delete": delete, "emphasis": emph,
                     "bgm": "upbeat", "notes": "本地规则方案（未使用 AI）"}, data)
