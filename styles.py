# -*- coding: utf-8 -*-
"""风格模板：一个风格 = 一套全片设置预设 + 一段给 AI 的剪辑规则。
来源：用户提供的《竖屏口播剪辑包装全案手册》《字幕样式 SOP》《BGM 规范》+ 剪辑师晨晨方法论，
取舍说明见 经验/口播包装规范.md。"""

STYLES = {
    "finance": {
        "name": "财经/商业 · 极简专业",
        "settings": {"fx": {"sub_in": "fade", "keyword": "color", "accent": "yellow"}, "speed": 1.05, "pause": 0.35, "color": {"preset": "cool"},
                     "sub": {"style": "soft", "max_chars": 10},
                     "audio": {"sfx": 0.5, "bgm": 0.08, "duck": True}},
        "bg": ["0x0b1626", "0x1d3352", "0x070b12"],
        "colors": "高亮色只用 yellow（金色，普通重点）和 blue（深蓝，数据/政策）；风险、亏损类才用 red。",
        "rules": """- 克制即专业：重点词约每 3～4 句 1 个，开头前 2 句可以密一点。
- 大字（big）全片 2～4 次，优先给数字用 count（数字滚动）；card 最多 1 次，pop 少用。
- 推近（zoom）全片 2～4 次，只给核心结论句。
- 音效只放在关键词上，用 ding / pop / tick，全片 4～8 个；不用 hit、whoosh、sparkle。
- 配图少：只在提到具体数据来源、行业场景时配，全片 2～4 张，优先 pip。
- bgm 用 chill（沉稳舒缓）；严肃话题可以 none。
- 标题像观点或结论，不要感叹号堆砌。
- 大字效果只用：count、card（最多 1 次）、type。
- 列举要点时做分点卡片（points）；结尾引导（ending）和主题相关。
""",
    },
    "news": {
        "name": "民生/资讯 · 稳重可信",
        "settings": {"fx": {"sub_in": "fade", "keyword": "color", "accent": "red"}, "speed": 1.0, "pause": 0.35, "color": {"preset": "base"},
                     "sub": {"style": "soft", "max_chars": 10},
                     "audio": {"sfx": 0.5, "bgm": 0.08, "duck": True}},
        "bg": ["0x111827", "0x26324a", "0x0b0f17"],
        "colors": "警示/风险用 red，政策/数据用 blue，其余重点用 yellow；全片最多两种。",
        "rules": """- 客观中立，不煽情：重点词约每 3 句 1 个。
- 大字全片 2～3 次（数字用 count），不用 card。推近 2～3 次。
- 音效全片 3～6 个，只用 ding / page / tick；不用 hit、sparkle。
- 配图可以多一点（事件、场景、人群），全片 3～6 张，帮助观众看懂事件。
- bgm 用 chill 或 none（严肃、负面事件用 none）。
- 大字效果只用：count、pop、type。
- 列举要点时做分点卡片（points）；结尾引导（ending）留空或很克制。
""",
    },
    "knowledge": {
        "name": "知识/成长 · 网感干货",
        "settings": {"fx": {"sub_in": "rise", "keyword": "marker", "accent": "orange"}, "speed": 1.15, "pause": 0.25, "color": {"preset": "base"},
                     "sub": {"style": "soft", "max_chars": 10},
                     "audio": {"sfx": 0.7, "bgm": 0.10, "duck": True}},
        "bg": ["0x0f1e1c", "0x1f3a36", "0x0a1211"],
        "colors": "重点用 orange（橙）或 yellow（金），警示用 red；全片最多两种。",
        "rules": """- 节奏快、重点清楚：重点词约每 2～3 句 1 个，开头前 2 句更密。
- 大字全片约每 5～7 句 1 次：数字用 count，核心概念/结论用 card，钩子句用 pop。
- 推近约每 4～5 句 1 次，不连续。
- 音效只放在关键词上（ding / pop / type / page / tick），全片约每 10 秒 1 个，同一种别连用 3 次。
- 配图约每 6～8 句 1 张，讲到具体东西、步骤时配。
- bgm 用 upbeat（轻快）。
- 大字效果只用：card、count、stack、type、pop。
- 列举要点时做分点卡片（points）；结尾引导（ending）和主题相关。
""",
    },
    "story": {
        "name": "故事/情感 · 共情",
        "settings": {"fx": {"sub_in": "char", "keyword": "color", "accent": "yellow"}, "speed": 1.0, "pause": 0.4, "color": {"preset": "warm"},
                     "sub": {"style": "soft", "max_chars": 10},
                     "audio": {"sfx": 0.4, "bgm": 0.12, "duck": True}},
        "bg": ["0x2a1c14", "0x4a3324", "0x150e0a"],
        "colors": "重点只用 yellow（暖金）；不用 blue。",
        "rules": """- 慢、留白：重点词约每 4 句 1 个，只给金句、转折。
- 大字全片 1～3 次，用 pop 或 card 给金句；不用 count（除非是关键数字）。
- 推近给情绪最重的 2～3 句。
- 音效极少：全片 0～3 个，只用 sparkle / ding；不用 hit、whoosh。
- 配图（空镜感）可以 4～6 张，画面要有情绪（街景、灯光、人物背影）。
- bgm 用 chill（舒缓）。
- 大字效果只用：glow、pop。
- 列举要点时做分点卡片（points）；结尾引导（ending）和主题相关。
""",
    },
    "hook": {
        "name": "大字报 · 热点强观点",
        "settings": {"fx": {"sub_in": "pop", "keyword": "marker", "accent": "red"}, "speed": 1.15, "pause": 0.2, "color": {"preset": "base"},
                     "sub": {"style": "soft", "max_chars": 9},
                     "audio": {"sfx": 0.8, "bgm": 0.10, "duck": True}},
        "bg": ["0x1a0b0b", "0x3a1414", "0x0d0606"],
        "colors": "重点用 yellow 和 red 两种。",
        "rules": """- 开头 3 秒必须抛结论/冲突，hook 优先选最狠的一句；开头前 3 句都做重点，第一句 big（pop 或 card）。
- 之后回归简约：重点词约每 3 句 1 个，大字全片 3～5 次。
- 推近约每 4 句 1 次。音效开头可以用 hit，后面用 ding / pop，全片约每 8 秒 1 个。
- 配图少，全片 0～3 张。bgm 用 hiphop 或 upbeat。
- 大字效果只用：stack、pop、count、card。
- 列举要点时做分点卡片（points）；结尾引导（ending）和主题相关。
""",
    },
    "chenchen": {
        "name": "晨晨精剪 · 包装密集",
        "settings": {"fx": {"sub_in": "pop", "keyword": "color", "accent": "yellow"}, "speed": 1.1, "pause": 0.2, "color": {"preset": "base"},
                     "sub": {"style": "soft", "max_chars": 11},
                     "audio": {"sfx": 0.9, "bgm": 0.10, "duck": True}},
        "bg": ["0x14141c", "0x2a2a3a", "0x0a0a10"],
        "colors": "全片以 yellow 为主，red 警示，blue 科技/数据。",
        "rules": """- 大约每 2～3 句保留句做 1 个重点，开头前 2 句更密。
- big 全片约每 4～6 句一次，开头第一句一般用；count 给数字、card 给核心概念，各不超过 3～4 次。
- zoom 约每 3～4 句一次，不连续。
- sfx 配合画面：whoosh 转场、hit 重音、ding 结论、pop 轻快、type 列举、page 下一点、sparkle 美好、tick 数字；同一种不连用超过 3 次。
- 配图约每 5～8 句 1 张。bgm 按内容选。
- 大字效果只用：全部（pop/count/card/glow/stack/type）。
- 列举要点时做分点卡片（points）；结尾引导（ending）和主题相关。
""",
    },
}

DEFAULT_STYLE = "knowledge"


def bg_colors(style):
    return STYLES.get(style, STYLES[DEFAULT_STYLE]).get("bg", ["0x101418", "0x26303a", "0x080a0c"])


def names():
    return {k: v["name"] for k, v in STYLES.items()}


def guide(style):
    """给 AI 的风格规则文本。style 为空时让 AI 自己判断赛道。"""
    if style in STYLES:
        s = STYLES[style]
        return f"\n\n## 本条视频风格：{s['name']}（必须遵守，优先于上面的通用密度）\n{s['rules']}\n- 配色：{s['colors']}"
    opts = "\n".join(f"- {k}：{v['name']}" for k, v in STYLES.items() if k != "chenchen")
    body = "\n\n".join(f"### {k}（{v['name']}）\n{v['rules']}\n- 配色：{v['colors']}" for k, v in STYLES.items() if k != "chenchen")
    return (f"\n\n## 风格：先判断这条视频属于哪个赛道，把 key 填进 style，再按该风格的规则剪\n{opts}\n\n{body}")


def preset_settings(style):
    """风格对应的全片设置（只给出风格关心的项，其余用默认值）。"""
    import settings as SET
    base = SET.normalize({})
    s = STYLES.get(style, STYLES[DEFAULT_STYLE])["settings"]
    out = dict(base, style=style if style in STYLES else DEFAULT_STYLE,
               speed=s.get("speed", base["speed"]), pause=s.get("pause", base["pause"]))
    if "color" in s:
        out["color"] = dict(SET.COLOR_PRESETS[s["color"]["preset"]], preset=s["color"]["preset"],
                            vignette=base["color"]["vignette"])
    out["sub"] = dict(base["sub"], **s.get("sub", {}))
    out["audio"] = dict(base["audio"], **s.get("audio", {}))
    out["fx"] = dict(base["fx"], **s.get("fx", {}))
    return SET.normalize(out)
