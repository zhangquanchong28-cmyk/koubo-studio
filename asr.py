# -*- coding: utf-8 -*-
"""语音转写：faster-whisper 词级时间戳 → 按停顿/标点切成短句（保留每个字的时间，供关键词动画对点）。"""
import os
import urllib.request

GAP = 0.30          # 字间停顿超过这么多秒就断句
MAX_LEN = 8.0       # 一句最长秒数
SENT_END = "，。！？；,.!?;"
PUNCT = SENT_END + "、：:…“”\"'（）() "
FILLER_CHARS = set("嗯呃额啊哦唔")

_model = None


def _choose_hf_endpoint():
    if os.environ.get("HF_ENDPOINT"):
        return
    try:
        urllib.request.urlopen("https://huggingface.co", timeout=6)
    except Exception:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


def load_model(name="medium", device="cpu", log=print):
    global _model
    if _model is None:
        _choose_hf_endpoint()
        from faster_whisper import WhisperModel
        log(f"加载语音模型 {name}（第一次会下载，约 1.5GB）…")
        _model = WhisperModel(name, device=device, compute_type="int8")
    return _model


def core(text):
    return text.strip().strip(PUNCT)


def build_phrases(words):
    phrases, cur = [], []

    def flush():
        if not cur:
            return
        text = "".join(w["text"] for w in cur).strip()
        c = core(text)
        phrases.append({
            "start": round(cur[0]["start"], 3),
            "end": round(cur[-1]["end"], 3),
            "text": text,
            "filler": c == "" or all(ch in FILLER_CHARS for ch in c),
            "words": [[round(w["start"], 3), round(w["end"], 3), w["text"].strip()] for w in cur],
        })
        cur.clear()

    for w in words:
        lead = w["text"].strip()
        if cur and lead[:1] in SENT_END:          # 标点粘在下一个词开头：挪回上一句末尾
            cut = len(lead) - len(lead.lstrip(SENT_END))
            cur[-1] = dict(cur[-1], text=cur[-1]["text"] + lead[:cut])
            w = dict(w, text=lead[cut:])
            if not w["text"]:
                continue
        if cur:
            prev = cur[-1]
            if (w["start"] - prev["end"] > GAP
                    or prev["text"].strip()[-1:] in SENT_END
                    or w["end"] - cur[0]["start"] > MAX_LEN):
                flush()
        cur.append(w)
    flush()
    for i, p in enumerate(phrases, 1):
        p["id"] = i
    return phrases


# 全角/小号标点统一成常用中文标点，否则断句认不出来
_PUNCT_MAP = str.maketrans({"﹐": "，", "﹑": "、", "﹒": "。", "﹔": "；", "﹕": "：", "﹖": "？", "﹗": "！",
                            "︐": "，", "︒": "。", "︔": "；", "︕": "！", "︖": "？"})
_t2s = None


def to_simplified(text):
    """Whisper 偶尔会输出繁体，统一转简体（逐词转换，字数不变，不影响逐字时间）。"""
    global _t2s
    if _t2s is None:
        try:
            from opencc import OpenCC
            _t2s = OpenCC("t2s").convert
        except ImportError:  # 没装就只修标点
            _t2s = lambda x: x  # noqa: E731
    out = _t2s(text.translate(_PUNCT_MAP))
    return out if len(out) == len(text) else text.translate(_PUNCT_MAP)


def transcribe(video_path, model_name="medium", device="cpu", log=print, progress=None):
    model = load_model(model_name, device, log)
    log("开始转写…")
    segments, info = model.transcribe(
        str(video_path), language="zh", word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        initial_prompt="以下是普通话口播，使用简体中文，并加上标点符号。")
    words = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                words.append({"start": w.start, "end": w.end, "text": to_simplified(w.word)})
        if progress and info.duration:
            progress(min(seg.end / info.duration, 1.0))
    if not words:
        raise RuntimeError("没有识别到语音，请确认视频里有人说话。")
    return {"duration": info.duration, "phrases": build_phrases(words)}


def keyword_time(phrase, keyword):
    """关键词在句中第一次出现时，它第一个字的开始时间（原片时间）。找不到返回句首。"""
    text, starts = "", []
    for s, _e, t in phrase.get("words", []):
        for ch in t:
            starts.append(s)
            text += ch
    i = text.find(keyword)
    if i < 0 or not starts:
        return phrase["start"]
    return starts[i]
