# -*- coding: utf-8 -*-
"""剪辑方案：调用 Claude（OpenRouter 或 Anthropic 直连）按口播方法论决定删哪些句、怎么包装。
没有 API Key 时退回到本地规则方案（只去语气词、给数字做强调）。"""
import json
import re
from pathlib import Path

import anthropic

import settings as SET
import styles

SFX_KINDS = ["whoosh", "hit", "ding", "pop", "type", "page", "sparkle", "tick", "none"]
COLORS = ["yellow", "red", "blue", "orange", "green"]
BGM_KINDS = ["upbeat", "chill", "hiphop", "none"]
EFFECTS = ["pop", "count", "card"]
LAYOUTS = ["pip", "full", "corner"]
IMG_NAME = re.compile(r"^[\w\-]{1,60}\.(?:jpg|jpeg|png|webp)$")

SYSTEM_PROMPT = """你是一名资深短视频口播剪辑师。你只负责做"剪辑方案"（JSON），由程序执行剪切和包装。

## 总原则
- 人声为根：口播的本质是听内容，所有包装为人声让路。可读性第一，网感第二。
- 克制即专业：财经、民生、知识类，信任感比花哨重要；一屏一个重点，不要同时出现多个强视觉元素。
- 节奏：前 3 秒抛钩子；之后每 10～15 秒给一个"注意力锚点"（重点词、大字、推近、配图之一）；全程"紧-松-紧"，不要匀速。

## 方案怎么写
1. 删除（delete）：只删口误、说错后重说的前一遍（保留最后说对的那一遍）、重复啰嗦、无意义语气词、明显跑题、开拍前后的准备话。
   保留内容要连贯、意思完整、不改变顺序。句间停顿由程序按风格保留，你不用为了"节奏快"去删正常的句子。
2. 开头钩子（hook）：如果全片有比开头更抓人的一句（结论、冲突、反常识、数字），填它的编号，程序会复制到最开头；开头已经够抓人就填 null。
3. 重点包装（emphasis），密度按下面"风格"的规则：
   - keywords：该句最该被记住的 1～2 个词（数字、结论、反转词、核心概念），必须是句子里一字不差的连续片段，2～8 个字。
   - color：yellow=金色（普通重点）、red=正红（警示/风险/负面）、blue=深蓝（数据/政策/科技）、orange=橙（成长/干货重点）、green=青绿（正向/增长）。
     全片高亮色不超过 2 种（按风格的配色要求）。
   - big=true：关键词在画面中上方单独出现（最强视觉），配合 effect：pop=放大弹出；count=数字滚动（只用于含阿拉伯数字的词，如"3000元""95%"）；card=3D 色卡（核心概念/结论/反转）。别连续两次用同一种。
   - zoom=true：这一句镜头缓慢推近，用在情绪/重点句，不要连续两句都推。
   - sfx：音效只配关键词和大字，正文不加。ding=提示/结论，pop=轻快弹出，type=打字/列举，page=翻页/下一点，tick=数字/时间，sparkle=美好，hit=重音（只用于开头钩子或强反转），whoosh=转场，none=不加。同一种不连用超过 3 次。
4. title：顶部常驻标题，≤14 个字，概括视频核心卖点；不适合就留空字符串。
5. 配图（broll）：在适合的句子上叠一张图，人声继续，用来解释内容、给观众视觉休息。数量按风格。
   - 选句子：说到具体的东西/场景/人群、举例子、讲数据来源时；纯观点句、开头第一句、钩子句不配；不要连续两句都配；配图的句子不再用 big。
   - desc：中文一句话说明画面。query：2～4 个英文单词（免费图库搜索用）。
   - prompt：英文生图提示词，写实摄影风格（主体、场景、光线、构图），结尾加 "photorealistic, no text, no watermark"；不要真实名人、品牌 logo、文字。
   - layout：pip=画中画（竖屏放在人物胸前、字幕上方，不挡脸，默认）；full=全屏（全片最多 2 次）；corner=右上角小窗。
6. 字幕纠错（text）：转写常有同音错字（如"减触高级感"→"剪出高级感"、"简映"→"剪映"、"对其"→"对齐"）。只改明显的错别字，不改说法、不润色，改后字数尽量一致；没错就不列。格式 {"编号": "改正后的整句"}。重点词按改正后的句子选。
7. bgm：upbeat=轻快，chill=舒缓沉稳，hiphop=有态度，none=严肃话题不要音乐。只用纯音乐，程序会把音乐压在人声之下并在说话时自动避让。

## 输出
只输出一个 JSON 对象，不要任何其他文字：
{"style": "风格key", "title": "...", "hook": 编号或null, "delete": [编号...],
 "emphasis": [{"id": 编号, "keywords": ["..."], "color": "yellow", "big": false, "effect": "pop", "zoom": false, "sfx": "none"}],
 "broll": [{"id": 编号, "desc": "...", "query": "...", "prompt": "...", "layout": "pip"}],
 "text": {"编号": "改正错别字后的整句"},
 "bgm": "chill", "notes": "一句话说明你的剪辑思路（含你判断的赛道）"}"""


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


def system_prompt(style=None):
    """通用原则 + 风格规则（指定了就按它，没指定让 AI 自己判断赛道）+ 用户经验。"""
    return SYSTEM_PROMPT + styles.guide(style) + user_rules()


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


def _call(client, cfg, user_text, log, system=None):
    kwargs = dict(
        model=model_id(cfg), max_tokens=16000, system=system or system_prompt(),
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


def ai_plan(cfg, data, notes="", log=print, style=None):
    client = make_client(cfg)
    if client is None:
        log("没有填 API Key，使用本地规则方案。")
        return rule_plan(data)
    user_text = (f"视频总时长 {data['duration']:.0f} 秒。\n"
                 f"用户要求：{notes or '无特别要求，按口播精品标准剪'}\n\n"
                 f"以下是转写短句（编号、开始时间、时长、文本）：\n{phrases_text(data['phrases'])}")
    style = style if style in styles.STYLES else None
    log(f"正在请 {model_id(cfg)} 生成剪辑方案（风格：{styles.STYLES[style]['name'] if style else 'AI 自动判断'}）…")
    text, usage = _ask(client, cfg, user_text, log, system=system_prompt(style))
    raw = _json_from(text)
    if style:
        raw["style"] = style
    plan = sanitize(raw, data)
    plan["usage"] = usage
    return plan


def _ask(client, cfg, user_text, log, system=None):
    """发一次请求，统一处理报错和计费，返回 (文本, 用量)。"""
    try:
        resp = _call(client, cfg, user_text, log, system)
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
    return text, {"input": u.input_tokens, "output": u.output_tokens, "usd": round(cost, 4)}


REVISE_PROMPT = """

## 现在的任务：按用户的一句话修改已有方案
你会拿到转写短句、当前方案 JSON 和用户的修改要求。
- 只改用户要求的部分，其他保持原样（包括 broll 里已有的 image/source/credit 字段，原样带上）。
- 用户说的"第 N 句"指编号 N。用户按"第几秒"说位置时，按句子开始时间找最接近的编号。
- 新加或改了描述的配图：image 留空字符串，程序会重新找图。
- 用户要改字幕上的字（错别字、换说法）：写进 text，{"编号": "整句新文字"}。
- 全片设置在 settings 里：speed 整体变速（1.0～1.5），canvas 画面比例（original/9:16/16:9/1:1/3:4），
  color 调色（preset: none/fresh/warm/cool/film/bright，brightness -0.3～0.3，contrast 0.5～1.8，saturation 0～2，temp 色温 3000～10000，sharpen 0～1.5），
  sub 字幕样式（size 0.6～1.8 倍，pos 距底部百分比 0=自动，style: outline/box/shadow，color 十六进制），
  audio（denoise 降噪，loudnorm 响度统一），fade（in 开头渐显，out 结尾淡出），export（res: original/1080/720，quality: high/standard）。
- 用户的话里如果有以后都要遵守的经验（"以后都…""记住…""我习惯…"），把它改写成一条具体的剪辑规则放进 remember，否则 remember 为空字符串。
只输出一个 JSON 对象，不要其他文字：
{"plan": {完整的新方案，格式同上}, "reply": "一两句中文，告诉用户你改了什么", "remember": ""}"""


def revise_plan(cfg, data, plan, instruction, log=print):
    """用户一句话改方案。返回 (新方案, 回复, 要记住的经验)。"""
    client = make_client(cfg)
    if client is None:
        raise RuntimeError("一句话改方案需要 AI，请先在设置里填 API Key。")
    cur = {k: v for k, v in plan.items() if k != "usage"}
    user_text = (f"视频总时长 {data['duration']:.0f} 秒。\n转写短句：\n{phrases_text(data['phrases'])}\n\n"
                 f"当前方案：\n{json.dumps(cur, ensure_ascii=False)}\n\n用户的修改要求：{instruction}")
    log(f"AI 正在按「{instruction[:40]}」修改方案…")
    text, usage = _ask(client, cfg, user_text, log, system=system_prompt(plan.get("style") or None) + REVISE_PROMPT)
    raw = _json_from(text)
    new = sanitize(raw.get("plan") or {}, data)
    new["notes"] = new.get("notes") or plan.get("notes", "")
    new["style"] = new.get("style") or plan.get("style", "")
    old_usd = (plan.get("usage") or {}).get("usd", 0)
    new["usage"] = dict(usage, usd=round(old_usd + usage["usd"], 4))
    return new, str(raw.get("reply") or "已修改。"), str(raw.get("remember") or "").strip(), usage["usd"]


def remember_rule(rule, section="通用"):
    """把一条经验追加到 经验/我的经验.md 对应小节。"""
    rule = rule.strip().lstrip("-").strip()
    if not rule:
        return
    EXP_FILE.parent.mkdir(exist_ok=True)
    text = EXP_FILE.read_text(encoding="utf-8") if EXP_FILE.is_file() else "# 我的剪辑经验\n\n## 通用\n"
    head = f"## {section}"
    if head not in text:
        text = text.rstrip() + f"\n\n{head}\n"
    i = text.index(head) + len(head)
    j = text.find("\n## ", i)
    j = len(text) if j < 0 else j
    block = text[i:j].rstrip() + f"\n- {rule}\n"
    text = text[:i] + block + ("\n" + text[j:].lstrip("\n") if j < len(text) else "")
    EXP_FILE.write_text(text, encoding="utf-8")


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
    fixes = {}
    for k, v in (raw.get("text") or {}).items():          # 字幕改字（AI 纠错或用户手改）
        pid = _int(k)
        v = str(v or "").strip()
        if pid in by_id and v and v != by_id[pid]["text"].strip() and len(v) <= max(8, 2 * len(by_id[pid]["text"])):
            fixes[str(pid)] = v[:200]
    shown = {pid: fixes.get(str(pid), p["text"]) for pid, p in by_id.items()}
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
        kws = [k for k in e.get("keywords", []) if isinstance(k, str) and k.strip() and k.strip() in shown[pid]][:2]
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
    trim = {}
    for k, v in (raw.get("trim") or {}).items():   # 时间线上手动微调的入出点（秒），每头最多 ±3 秒
        pid = _int(k)
        try:
            ds, de = (max(-3.0, min(3.0, round(float(x), 3))) for x in v)
        except (TypeError, ValueError):
            continue
        if pid in by_id and (ds or de):
            trim[str(pid)] = [ds, de]
    for e in emph:                       # 配图的句子不再弹大字，避免画面打架
        if e["id"] in bseen:
            e["big"] = False
    return {
        "style": raw.get("style") if raw.get("style") in styles.STYLES else "",
        "title": str(raw.get("title") or "")[:20],
        "hook": hook, "delete": delete,
        "emphasis": sorted(emph, key=lambda x: x["id"]),
        "broll": sorted(broll, key=lambda x: x["id"]),
        "trim": trim,
        "text": fixes,
        "settings": SET.normalize(raw.get("settings")),
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
