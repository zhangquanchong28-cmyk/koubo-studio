// 时间线编辑器：上面实时预览（跳过删掉的句子、叠加字幕/大字/配图/推近的近似效果），
// 下面多轨时间线（口播 / 包装 / 配图 / 音效），右边属性面板。所有修改都改在方案（plan）上，
// 出片仍由 packer.py 完成，这里的预览是近似效果。常量与 packer.py 保持一致。
(function () {
  "use strict";
  const PAD_IN = 0.08, PAD_OUT = 0.12, MERGE = 0.15, ZOOM_TO = 1.10, ZOOM_SEC = 0.45, BROLL_MIN = 2.2;
  const HEX = {yellow: "#FFD400", red: "#FF3B30", blue: "#3EA6FF"};
  const SFX_LEAD = {whoosh: 0.12};
  const keep = {sel: null, pxps: null, time: 0, pid: null, scroll: 0};   // 重新渲染之间保留的视图状态
  let S = null;

  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const fmt = t => { t = Math.max(0, t); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`; };
  const clean = t => String(t || "").replace(/[，。！？；、,.!?;：:]/g, " ").replace(/\s+/g, " ").trim();

  // ------------------------------------------------------------ 方案读写
  function maps() {
    const p = S.wp;
    S.del = new Set(p.delete || []);
    S.emph = Object.fromEntries((p.emphasis || []).map(e => [e.id, e]));
    S.br = Object.fromEntries((p.broll || []).map(b => [b.id, b]));
    p.trim = p.trim || {};
  }
  const gone = ph => S.del.has(ph.id) || ph.filler;
  const shown = ph => (S.wp.text || {})[String(ph.id)] || ph.text;
  const trimOf = id => S.wp.trim[String(id)] || [0, 0];

  function spanOf(i) {   // 与 packer.build_timeline.span 相同的规则
    const P = S.phrases, p = P[i], [ds, de] = trimOf(p.id), moved = ds || de;
    let lo = 0, hi = S.dur;
    if (i > 0) { const q = P[i - 1]; lo = moved && gone(q) ? q.start : q.end; }
    if (i + 1 < P.length) { const q = P[i + 1]; hi = moved && gone(q) ? q.end : q.start; }
    return [Math.max(lo, p.start - PAD_IN + ds, 0), Math.min(hi, p.end + PAD_OUT + de, S.dur)];
  }

  function buildRanges() {
    const out = [];
    S.phrases.forEach((p, i) => {
      if (gone(p)) return;
      const [s, e] = spanOf(i);
      if (e <= s) return;
      const z = !!(S.emph[p.id] || {}).zoom, last = out[out.length - 1];
      if (last && last.lastI === i - 1 && !z && !last.zoom && s - last.e < MERGE) { last.e = e; last.ids.push(p.id); last.lastI = i; }
      else out.push({s, e, ids: [p.id], zoom: z, lastI: i});
    });
    let acc = 0;
    for (const r of out) { r.off = acc; acc += r.e - r.s; }
    S.ranges = out;
    S.total = acc;
  }

  function kwTime(p, kw) {   // 没有逐字时间时按字数比例估计关键词出现的时间
    const t = shown(p), i = t.indexOf(kw);
    if (i < 0) return p.start;
    return p.start + (p.end - p.start) * (i / Math.max(1, t.length));
  }

  function remember() {   // 记一步历史（每次修改后调用）
    const now = JSON.stringify(S.wp);
    if (now === S.last) return;
    S.undo.push(S.last); if (S.undo.length > 100) S.undo.shift();
    S.redo = []; S.last = now;
  }
  function touch() { maps(); buildRanges(); drawTracks(); drawProps(); remember(); S.ctx.onDirty(); }
  function travel(from, to) {
    if (!from.length) return;
    to.push(S.last); S.last = from.pop();
    S.wp = JSON.parse(S.last);
    maps(); buildRanges(); drawTracks(); drawProps();
    S.el.querySelector(".tl-title").value = S.wp.title || "";
    S.el.querySelector(".tl-bgm").value = S.wp.bgm || "upbeat";
    S.ctx.onDirty();
  }
  const undo = () => travel(S.undo, S.redo), redo = () => travel(S.redo, S.undo);

  function setEmph(id, patch) {
    const p = S.wp, list = p.emphasis = p.emphasis || [];
    let e = list.find(x => x.id === id);
    if (!e) { e = {id, keywords: [], color: "yellow", big: false, effect: "pop", zoom: false, sfx: "none"}; list.push(e); }
    Object.assign(e, patch);
    if (!e.keywords.length && !e.big && !e.zoom && e.sfx === "none") p.emphasis = list.filter(x => x !== e);
    p.emphasis.sort((a, b) => a.id - b.id);
  }
  function setBroll(id, patch) {
    const p = S.wp, list = p.broll = p.broll || [];
    let b = list.find(x => x.id === id);
    if (patch.layout === "") { p.broll = list.filter(x => x.id !== id); return; }
    if (!b) {
      const ph = S.phrases.find(x => x.id === id);
      b = {id, layout: "pip", desc: ph ? ph.text : "", query: "", prompt: "", image: "", source: "", credit: ""};
      list.push(b);
    }
    if ("desc" in patch && patch.desc !== b.desc) Object.assign(b, {query: "", prompt: "", image: ""});   // 改了描述要重新找图
    Object.assign(b, patch);
    p.broll.sort((a, b2) => a.id - b2.id);
  }
  function toggleDelete(id) {
    const ph = S.phrases.find(x => x.id === id);
    if (!ph || ph.filler) return;
    const d = new Set(S.wp.delete || []);
    d.has(id) ? d.delete(id) : d.add(id);
    S.wp.delete = [...d].sort((a, b) => a - b);
    if (S.wp.hook === id && d.has(id)) S.wp.hook = null;
    touch();
  }

  // ------------------------------------------------------------ 界面骨架
  function mount(el, ctx) {
    destroy();
    const wp = JSON.parse(JSON.stringify(ctx.plan));
    S = {el, ctx, wp, phrases: ctx.transcript.phrases, dur: +ctx.transcript.duration, W: 1080, H: 1920,
      undo: [], redo: [], last: JSON.stringify(wp), st: ctx.settings || {}};
    if (keep.pid !== ctx.pid) Object.assign(keep, {sel: null, pxps: null, time: 0, pid: ctx.pid, scroll: 0});
    maps(); buildRanges();
    el.innerHTML = `
      <div class="tl-bar">
        <button class="tl-play" title="播放/暂停（空格）">▶</button>
        <button class="tl-undo" title="撤销（Ctrl+Z）">↶</button><button class="tl-redo" title="重做（Ctrl+Shift+Z）">↷</button>
        <span class="tl-time mono">0:00.0 / 0:00.0</span>
        <label>标题 <input class="tl-title" maxlength="20" style="width:180px"></label>
        <label>BGM <select class="tl-bgm">${Object.entries(ctx.BGM).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <span style="flex:1"></span>
        <label class="muted">缩放 <input type="range" class="tl-zoom" min="4" max="160" step="1"></label>
      </div>
      <div class="tl-main">
        <div class="tl-stage-wrap"><div class="tl-stage">
          <video class="tl-bg" muted playsinline preload="auto" src="/api/p/${ctx.pid}/file/source"></video>
          <video class="tl-video" preload="auto" playsinline src="/api/p/${ctx.pid}/file/source"></video>
          <div class="ov ov-broll"></div><div class="ov ov-pop"></div><div class="ov ov-title"></div><div class="ov ov-sub"></div>
        </div><p class="muted tl-hint">预览是近似效果（字体、动画以出片为准）。空格播放，Delete 删除/恢复，←/→ 上一句/下一句，拖句子两头微调入出点。</p></div>
        <div class="tl-props"></div>
      </div>
      <div class="tl-scroll"><div class="tl-inner">
        <div class="tl-ruler"></div>
        <div class="tl-track" data-k="talk"><span class="tl-lab">口播</span></div>
        <div class="tl-track" data-k="fx"><span class="tl-lab">包装</span></div>
        <div class="tl-track" data-k="br"><span class="tl-lab">配图</span></div>
        <div class="tl-track" data-k="sfx"><span class="tl-lab">音效</span></div>
        <div class="tl-head"></div>
      </div></div>`;
    S.v = el.querySelector(".tl-video");
    S.bg = el.querySelector(".tl-bg");
    S.stage = el.querySelector(".tl-stage");
    S.scroll = el.querySelector(".tl-scroll");
    S.inner = el.querySelector(".tl-inner");
    const title = el.querySelector(".tl-title"), bgm = el.querySelector(".tl-bgm"), zoom = el.querySelector(".tl-zoom");
    title.value = wp.title || ""; bgm.value = wp.bgm || "upbeat";
    title.oninput = () => { S.wp.title = title.value; ctx.onDirty(); };
    title.onchange = () => remember();
    bgm.onchange = () => { S.wp.bgm = bgm.value; remember(); ctx.onDirty(); };
    S.pxps = keep.pxps || clamp((S.scroll.clientWidth - 60) / Math.max(1, S.dur), 4, 160);
    zoom.value = S.pxps;
    zoom.oninput = () => { const c = timeAtCenter(); S.pxps = keep.pxps = +zoom.value; drawTracks(); centerOn(c); };
    el.querySelector(".tl-play").onclick = togglePlay;
    el.querySelector(".tl-undo").onclick = undo;
    el.querySelector(".tl-redo").onclick = redo;
    S.v.addEventListener("loadedmetadata", () => {
      S.srcW = S.v.videoWidth || S.W; S.srcH = S.v.videoHeight || S.H;
      applySettings();
      S.v.currentTime = keep.time || (S.ranges[0] ? S.ranges[0].s : 0);
    });
    S.v.addEventListener("error", () => {
      el.querySelector(".tl-hint").innerHTML = "⚠ 浏览器播放不了这个视频格式（常见于苹果手机的 HEVC/H.265），时间线照样能编辑，出片不受影响。";
    });
    S.ro = new ResizeObserver(layout); S.ro.observe(S.stage);
    S.scroll.scrollLeft = keep.scroll;
    S.scroll.addEventListener("scroll", () => { keep.scroll = S.scroll.scrollLeft; });
    bindTimeline();
    S.onKey = onKey; document.addEventListener("keydown", S.onKey);
    S.sel = keep.sel;
    drawTracks(); drawProps();
    S.raf = requestAnimationFrame(tick);
  }

  const CANVAS = {"9:16": [1080, 1920], "16:9": [1920, 1080], "1:1": [1080, 1080], "3:4": [1080, 1440]};
  function applySettings() {   // 全片设置的近似预览
    if (!S) return;
    const st = S.st || {}, c = st.color || {}, sub = st.sub || {};
    const sw = S.srcW || S.W, sh = S.srcH || S.H, cv = CANVAS[st.canvas];
    [S.W, S.H] = cv && Math.abs(cv[0] / cv[1] - sw / sh) > 0.01 ? cv : [sw, sh];
    S.stage.style.aspectRatio = `${S.W} / ${S.H}`;
    S.stage.classList.toggle("canvas", !!(cv && (S.W !== sw || S.H !== sh)));
    const sp = st.speed || 1;
    S.v.playbackRate = sp; S.bg.playbackRate = sp;
    const t = c.temp || 6500;
    const warm = t < 6500 ? `sepia(${((6500 - t) / 3500 * 0.45).toFixed(3)})` : "";
    const cool = t > 6500 ? `hue-rotate(${(-(t - 6500) / 3500 * 10).toFixed(1)}deg) saturate(${(1 - (t - 6500) / 3500 * 0.1).toFixed(3)})` : "";
    const f = `brightness(${(1 + (c.brightness || 0) * 1.4).toFixed(3)}) contrast(${c.contrast || 1}) saturate(${c.saturation ?? 1}) ${warm} ${cool}`;
    S.v.style.filter = f;
    S.bg.style.filter = f + " blur(18px) brightness(0.9)";
    const ss = S.stage.style;
    ss.setProperty("--subscale", sub.size || 1);
    ss.setProperty("--subcolor", sub.color || "#FFFFFF");
    S.stage.dataset.substyle = sub.style || "outline";
    S.subPos = sub.pos || 0;
    layout();
    S.el.querySelector(".ov-sub").dataset.k = "";      // 让字幕重画
  }
  function setSettings(st) { if (!S) return; S.st = JSON.parse(JSON.stringify(st || {})); applySettings(); }

  function destroy() {
    if (!S) return;
    cancelAnimationFrame(S.raf);
    document.removeEventListener("keydown", S.onKey);
    if (S.ro) S.ro.disconnect();
    if (S.v) { keep.time = S.v.currentTime; S.v.pause(); S.v.removeAttribute("src"); S.v.load(); }
    if (S.bg) { S.bg.pause(); S.bg.removeAttribute("src"); S.bg.load(); }
    S = null;
  }

  function getPlan() {
    const p = JSON.parse(JSON.stringify(S.wp));
    p.emphasis = (p.emphasis || []).filter(e => e.keywords.length || e.big || e.zoom || e.sfx !== "none");
    return p;
  }

  // ------------------------------------------------------------ 预览
  function layout() {
    if (!S) return;
    const k = S.stage.clientHeight / S.H, portrait = S.H >= S.W, Sz = Math.min(S.W, S.H) * 0.058 * k;
    S.k = k;
    const st = S.stage.style;
    st.setProperty("--S", Sz + "px");
    st.setProperty("--sub-b", (S.subPos ? S.subPos : (portrait ? 17 : 7)) + "%");
    st.setProperty("--pop-t", (portrait ? 27 : 16) + "%");
    st.setProperty("--title-t", (portrait ? 7 : 5) + "%");
  }

  function brollBox(layoutName) {   // 与 packer.broll_box 相同（返回画面百分比）
    const W = S.W, H = S.H, portrait = H >= W;
    let x, y, w, h;
    if (layoutName === "full") { x = 0; y = 0; w = W; h = H; }
    else if (layoutName === "corner") { w = (portrait ? W : H) * 0.36; h = w; x = W - w - W * 0.05; y = H * (portrait ? 0.14 : 0.08); }
    else if (portrait) { w = W * 0.88; h = w * 0.75; const Sz = Math.min(W, H) * 0.058; y = H - H * 0.17 - Sz * 1.7 - h; x = (W - w) / 2; }
    else { h = H * 0.6; w = h * 4 / 3; x = W - w - W * 0.04; y = (H - h) / 2 - H * 0.06; }
    return {left: x / W * 100, top: y / H * 100, width: w / W * 100, height: h / H * 100};
  }

  function rangeAt(t) { return S.ranges.find(r => t >= r.s - 0.01 && t < r.e); }
  function outTime(t) { const r = rangeAt(t); if (r) return r.off + (t - r.s); const n = S.ranges.find(r => r.s > t); return n ? n.off : S.total; }

  function togglePlay() {
    if (!S) return;
    if (S.v.paused) {
      const t = S.v.currentTime;
      if (!rangeAt(t)) { const n = S.ranges.find(r => r.s > t) || S.ranges[0]; if (n) S.v.currentTime = n.s; }
      S.v.play().catch(() => {});
    } else S.v.pause();
  }

  function tick() {
    if (!S) return;
    const v = S.v;
    let t = v.currentTime;
    if (!v.paused && !v.seeking) {     // 播放时跳过删掉的部分
      if (!rangeAt(t)) {
        const n = S.ranges.find(r => r.s > t);
        if (n) { v.currentTime = n.s; t = n.s; } else v.pause();
      }
    }
    keep.time = t;
    if (S.stage.classList.contains("canvas")) {
      if (Math.abs(S.bg.currentTime - t) > 0.25) S.bg.currentTime = t;
      if (v.paused !== S.bg.paused) v.paused ? S.bg.pause() : S.bg.play().catch(() => {});
    }
    S.el.querySelector(".tl-play").textContent = v.paused ? "▶" : "❚❚";
    const sp = (S.st && S.st.speed) || 1;
    S.el.querySelector(".tl-time").textContent = `${fmt(outTime(t) / sp)} / ${fmt(S.total / sp)}  （原片 ${fmt(t)}）`;
    S.inner.querySelector(".tl-head").style.left = (t * S.pxps) + "px";
    if (!v.paused) follow(t);
    overlay(t);
    S.raf = requestAnimationFrame(tick);
  }

  function follow(t) {   // 播放头快出屏时自动滚动
    const x = t * S.pxps, sl = S.scroll.scrollLeft, w = S.scroll.clientWidth;
    if (x > sl + w - 60 || x < sl) S.scroll.scrollLeft = x - 80;
  }

  function overlay(t) {
    const ph = S.phrases.find(p => !gone(p) && t >= p.start - 0.02 && t <= p.end + 0.08);
    const r = rangeAt(t);
    // 推近
    const z = r && r.zoom ? 1 + (ZOOM_TO - 1) * clamp((t - r.s) / ZOOM_SEC, 0, 1) : 1;
    S.v.style.transform = z === 1 ? "" : `scale(${z})`;
    // 标题
    const ti = S.el.querySelector(".ov-title");
    const tt = S.wp.title || "";
    if (ti.dataset.t !== tt) { ti.dataset.t = tt; ti.innerHTML = tt ? `<span>${esc(tt)}</span>` : ""; }
    // 字幕
    const sub = S.el.querySelector(".ov-sub");
    const key = ph ? ph.id + "|" + shown(ph) + JSON.stringify(S.emph[ph.id] || {}) : "";
    if (sub.dataset.k !== key) {
      sub.dataset.k = key;
      if (!ph) sub.innerHTML = "";
      else {
        let h = esc(clean(shown(ph)));
        const e = S.emph[ph.id];
        if (e) for (const kw of e.keywords || []) { const c = esc(clean(kw)); if (c) h = h.replace(c, `<b style="color:${HEX[e.color] || HEX.yellow}">${c}</b>`); }
        sub.innerHTML = `<span>${h}</span>`;
      }
    }
    // 配图
    const bo = S.el.querySelector(".ov-broll");
    let bk = "";
    const bp = S.phrases.find(p => !gone(p) && S.br[p.id] && t >= p.start && t <= Math.max(p.end, p.start + BROLL_MIN));
    if (bp) {
      const b = S.br[bp.id], box = brollBox(b.layout);
      bk = bp.id + b.layout + b.image;
      if (bo.dataset.k !== bk) {
        bo.dataset.k = bk;
        bo.className = "ov ov-broll on " + b.layout;
        Object.assign(bo.style, {left: box.left + "%", top: box.top + "%", width: box.width + "%", height: box.height + "%"});
        bo.innerHTML = b.image ? `<img src="/api/p/${S.ctx.pid}/file/broll:${encodeURIComponent(b.image)}">`
          : `<div class="ph">配图（还没找图）<br>${esc(b.desc)}</div>`;
      }
    } else if (bo.dataset.k) { bo.dataset.k = ""; bo.className = "ov ov-broll"; bo.innerHTML = ""; }
    // 大字
    const po = S.el.querySelector(".ov-pop");
    let pk = "", html = "";
    if (ph && !S.br[ph.id]) {
      const e = S.emph[ph.id];
      if (e && e.big && e.keywords.length) {
        const kw = clean(e.keywords[0]), kt = kwTime(ph, e.keywords[0]);
        if (t >= kt && t <= Math.max(ph.end + 0.25, kt + 1.0)) {
          const fx = e.effect || "pop", col = HEX[e.color] || HEX.yellow;
          pk = ph.id + fx + kw + e.color;
          if (fx === "count" && /\d/.test(kw)) {
            const m = kw.match(/\d+(?:\.\d+)?/), dec = m[0].includes(".") ? m[0].split(".")[1].length : 0;
            const k = clamp((t - kt) / 0.7, 0, 1), v = +m[0] * (1 - Math.pow(1 - k, 3));
            html = `<span class="big" style="color:${col}">${esc(kw.slice(0, m.index))}${v.toFixed(dec)}${esc(kw.slice(m.index + m[0].length))}</span>`;
            pk += Math.round(k * 20);
          } else if (fx === "card") {
            const fg = e.color === "yellow" ? "#181818" : "#fff";
            html = `<span class="card3d" style="background:${col};color:${fg}">${esc(kw)}</span>`;
          } else html = `<span class="big pop" style="color:${col}">${esc(kw)}</span>`;
        }
      }
    }
    if (po.dataset.k !== pk) { po.dataset.k = pk; po.innerHTML = html; }
  }

  // ------------------------------------------------------------ 时间线
  function drawTracks() {
    if (!S) return;
    const px = S.pxps, W = Math.ceil(S.dur * px) + 80;
    S.inner.style.width = W + "px";
    // 刻度
    const step = px > 60 ? 1 : px > 25 ? 2 : px > 10 ? 5 : 10;
    let ruler = "";
    for (let s = 0; s <= S.dur; s += step) ruler += `<i style="left:${s * px}px">${fmt(s).replace(/\.0$/, "")}</i>`;
    S.inner.querySelector(".tl-ruler").innerHTML = ruler;
    // 口播
    const talk = S.phrases.map((p, i) => {
      const off = gone(p), [s, e] = off ? [p.start, p.end] : spanOf(i), tr = trimOf(p.id);
      const cls = ["blk", off ? "off" : "", p.filler ? "filler" : "", S.sel === p.id ? "sel" : "", S.wp.hook === p.id ? "hook" : "",
        (tr[0] || tr[1]) ? "trimmed" : ""].join(" ");
      return `<div class="${cls}" data-id="${p.id}" style="left:${s * px}px;width:${Math.max(2, (e - s) * px - 1)}px" title="#${p.id} ${esc(p.text)}">
        ${off ? "" : '<b class="h h-l"></b><b class="h h-r"></b>'}<span>${p.id} ${esc(p.text)}</span></div>`;
    }).join("");
    // 包装：推近条 + 大字/重点词标记
    let fx = "";
    for (const p of S.phrases) {
      const e = S.emph[p.id];
      if (!e || gone(p)) continue;
      if (e.zoom) fx += `<div class="zm" style="left:${p.start * px}px;width:${Math.max(2, (p.end - p.start) * px)}px" title="推近"></div>`;
      if (e.keywords.length) {
        const kt = kwTime(p, e.keywords[0]);
        const lab = e.big ? ({pop: "大字", count: "滚动", card: "卡片"})[e.effect || "pop"] : "重点";
        fx += `<div class="chip ${e.big ? "big" : ""}" data-id="${p.id}" style="left:${kt * px}px;border-color:${HEX[e.color] || HEX.yellow}" title="${esc(e.keywords.join("、"))}">${lab}·${esc(e.keywords[0])}</div>`;
      }
    }
    // 配图
    let br = "";
    for (const p of S.phrases) {
      const b = S.br[p.id];
      if (!b || gone(p)) continue;
      const w = Math.max(p.end, p.start + BROLL_MIN) - p.start;
      const bg = b.image ? `background-image:url('/api/p/${S.ctx.pid}/file/broll:${encodeURIComponent(b.image)}')` : "";
      br += `<div class="brb ${b.image ? "" : "empty"}" data-id="${p.id}" style="left:${p.start * px}px;width:${w * px - 1}px;${bg}" title="${esc(b.desc)}"><span>${({pip: "画中画", full: "全屏", corner: "小窗"})[b.layout] || ""}</span></div>`;
    }
    // 音效
    let sx = "";
    for (const p of S.phrases) {
      const e = S.emph[p.id];
      if (!e || gone(p) || !e.sfx || e.sfx === "none") continue;
      const t = (e.keywords.length ? kwTime(p, e.keywords[0]) : p.start) - (SFX_LEAD[e.sfx] ?? 0.03);
      sx += `<div class="sfx" data-id="${p.id}" style="left:${t * px}px" title="${esc(S.ctx.SFX[e.sfx] || e.sfx)}">◆ ${esc(S.ctx.SFX[e.sfx] || e.sfx)}</div>`;
    }
    const set = (k, h) => { const tr = S.inner.querySelector(`.tl-track[data-k="${k}"]`); tr.querySelectorAll(":scope > :not(.tl-lab)").forEach(n => n.remove()); tr.insertAdjacentHTML("beforeend", h); };
    set("talk", talk); set("fx", fx); set("br", br); set("sfx", sx);
  }

  function timeAtCenter() { return (S.scroll.scrollLeft + S.scroll.clientWidth / 2) / S.pxps; }
  function centerOn(t) { S.scroll.scrollLeft = t * S.pxps - S.scroll.clientWidth / 2; }

  function bindTimeline() {
    const inner = S.inner;
    const tAt = ev => (ev.clientX - inner.getBoundingClientRect().left) / S.pxps;
    inner.addEventListener("mousedown", ev => {
      const h = ev.target.closest(".h"), blk = ev.target.closest("[data-id]");
      if (h && blk) {   // 拖句子两头：微调入出点
        ev.preventDefault();
        const id = +blk.dataset.id, side = h.classList.contains("h-l") ? 0 : 1, x0 = ev.clientX, tr0 = trimOf(id).slice();
        select(id, false);
        const move = e2 => {
          const d = (e2.clientX - x0) / S.pxps, tr = tr0.slice();
          tr[side] = clamp(Math.round((tr0[side] + d) * 100) / 100, -3, 3);
          if (tr[0] || tr[1]) S.wp.trim[String(id)] = tr; else delete S.wp.trim[String(id)];
          buildRanges(); drawTracks(); drawProps();
          const i = S.phrases.findIndex(p => p.id === id), sp = spanOf(i);
          S.v.currentTime = side ? Math.max(sp[0], sp[1] - 0.6) : sp[0];
        };
        const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); remember(); S.ctx.onDirty(); };
        document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
        return;
      }
      if (blk) {
        const id = +blk.dataset.id;
        select(id, true);
        return;
      }
      S.v.currentTime = clamp(tAt(ev), 0, S.dur);   // 点空白处 / 刻度：跳转
    });
    inner.addEventListener("dblclick", ev => { const b = ev.target.closest(".blk"); if (b) toggleDelete(+b.dataset.id); });
  }

  function select(id, seek) {
    S.sel = keep.sel = id;
    const i = S.phrases.findIndex(p => p.id === id);
    if (seek && i >= 0) { S.v.pause(); S.v.currentTime = gone(S.phrases[i]) ? S.phrases[i].start : spanOf(i)[0]; }
    drawTracks(); drawProps();
  }

  function onKey(ev) {
    if (!S || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (mod && (ev.key === "z" || ev.key === "Z")) { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
    if (mod && (ev.key === "y" || ev.key === "Y")) { ev.preventDefault(); redo(); return; }
    if (ev.code === "Space") { ev.preventDefault(); togglePlay(); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && S.sel != null) { ev.preventDefault(); toggleDelete(S.sel); }
    else if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
      ev.preventDefault();
      const i = S.phrases.findIndex(p => p.id === S.sel), n = clamp(i + (ev.key === "ArrowRight" ? 1 : -1), 0, S.phrases.length - 1);
      select(S.phrases[i < 0 ? 0 : n].id, true);
      const p = S.phrases[n];
      if (p && (p.start * S.pxps < S.scroll.scrollLeft || p.start * S.pxps > S.scroll.scrollLeft + S.scroll.clientWidth - 40)) centerOn(p.start);
    }
  }

  // ------------------------------------------------------------ 属性面板
  function drawProps() {
    if (!S) return;
    const box = S.el.querySelector(".tl-props"), C = S.ctx;
    const p = S.phrases.find(x => x.id === S.sel);
    if (!p) {
      box.innerHTML = `<h3>属性</h3><p class="muted">点时间线上的一句话来编辑。<br><br>成片约 <b>${fmt(S.total / ((S.st && S.st.speed) || 1))}</b>，原片 ${fmt(S.dur)}。<br>
        保留 ${S.phrases.filter(x => !gone(x)).length} / ${S.phrases.length} 句。</p>`;
      return;
    }
    const e = S.emph[p.id] || {keywords: [], color: "yellow", big: false, effect: "pop", zoom: false, sfx: "none"};
    const b = S.br[p.id] || {layout: ""}, tr = trimOf(p.id), off = gone(p);
    const opt = (o, v) => Object.entries(o).map(([k, n]) => `<option value="${k}" ${k === v ? "selected" : ""}>${n}</option>`).join("");
    box.innerHTML = `<h3>#${p.id} <span class="muted mono">${fmt(p.start)}–${fmt(p.end)}</span></h3>
      <p class="tl-text ${off ? "off" : ""}">${esc(p.text)}</p>
      <label>字幕文字（改错字，只影响字幕显示）<input class="pp-text" value="${esc(shown(p))}"></label>
      <div class="row">
        <label><input type="checkbox" class="pp-keep" ${off ? "" : "checked"} ${p.filler ? "disabled" : ""}> 保留</label>
        <label><input type="checkbox" class="pp-hook" ${S.wp.hook === p.id ? "checked" : ""} ${off ? "disabled" : ""}> 开头钩子</label>
      </div>
      <label>重点词（逗号分隔，必须是原句里的字）<input class="pp-kw" value="${esc((e.keywords || []).join("，"))}"></label>
      <div class="row">
        <label>颜色 <select class="pp-color">${opt(C.COLORS, e.color)}</select></label>
        <label>大字 <select class="pp-big">${opt(C.BIGFX, e.big ? (e.effect || "pop") : "")}</select></label>
      </div>
      <div class="row">
        <label><input type="checkbox" class="pp-zoom" ${e.zoom ? "checked" : ""}> 推近</label>
        <label>音效 <select class="pp-sfx">${opt(C.SFX, e.sfx)}</select></label>
      </div>
      <label>配图 <select class="pp-blay">${opt(C.LAYOUT, b.layout || "")}</select></label>
      ${b.layout ? `<label>画面描述<input class="pp-bdesc" value="${esc(b.desc || "")}"></label>
        ${b.image ? `<img class="pp-img" src="/api/p/${C.pid}/file/broll:${encodeURIComponent(b.image)}">` : `<p class="muted">还没有图：保存后点「准备配图」</p>`}` : ""}
      <p class="muted">入点 ${tr[0] > 0 ? "+" : ""}${tr[0].toFixed(2)}s · 出点 ${tr[1] > 0 ? "+" : ""}${tr[1].toFixed(2)}s
        ${tr[0] || tr[1] ? '<button class="mini pp-reset">复位</button>' : ""}<br><small>在时间线上拖这句的两头可以微调</small></p>`;
    const q = s => box.querySelector(s);
    q(".pp-keep").onchange = () => toggleDelete(p.id);
    q(".pp-hook").onchange = ev => { S.wp.hook = ev.target.checked ? p.id : null; touch(); };
    q(".pp-kw").onchange = ev => {
      const kws = ev.target.value.split(/[,，、\s]+/).filter(Boolean).filter(k => shown(p).includes(k)).slice(0, 2);
      if (ev.target.value.trim() && !kws.length) alert("重点词必须是这句话里一字不差的词");
      setEmph(p.id, {keywords: kws, big: kws.length ? !!e.big : false}); touch();
    };
    q(".pp-text").onchange = ev => {
      const v = ev.target.value.trim();
      S.wp.text = S.wp.text || {};
      if (!v || v === p.text.trim()) delete S.wp.text[String(p.id)]; else S.wp.text[String(p.id)] = v;
      const e2 = S.emph[p.id];   // 改字后重点词不在句子里了就去掉
      if (e2 && e2.keywords.some(k => !shown(p).includes(k))) setEmph(p.id, {keywords: e2.keywords.filter(k => shown(p).includes(k))});
      touch();
    };
    q(".pp-color").onchange = ev => { setEmph(p.id, {color: ev.target.value}); touch(); };
    q(".pp-big").onchange = ev => {
      const v = ev.target.value;
      if (v && !(e.keywords || []).length) { alert("先填重点词，大字显示的就是重点词"); ev.target.value = ""; return; }
      setEmph(p.id, {big: !!v, effect: v || "pop"}); touch();
    };
    q(".pp-zoom").onchange = ev => { setEmph(p.id, {zoom: ev.target.checked}); touch(); };
    q(".pp-sfx").onchange = ev => { setEmph(p.id, {sfx: ev.target.value}); touch(); };
    q(".pp-blay").onchange = ev => { setBroll(p.id, {layout: ev.target.value}); touch(); };
    if (q(".pp-bdesc")) q(".pp-bdesc").onchange = ev => { setBroll(p.id, {desc: ev.target.value}); touch(); };
    if (q(".pp-reset")) q(".pp-reset").onclick = () => { delete S.wp.trim[String(p.id)]; touch(); };
  }

  window.Timeline = {mount, destroy, getPlan, setSettings, active: () => !!S, currentTime: () => (S ? S.v.currentTime : 0)};
})();
