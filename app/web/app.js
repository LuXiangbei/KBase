/* KBase 前端：零构建、fetch + ReadableStream 解析 SSE、三端自适应 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const els = {
    messages: $("#messages"),
    input: $("#input"),
    composer: $("#composer"),
    send: $("#sendBtn"),
    docList: $("#docList"),
    docCount: $("#docCount"),
    indexInfo: $("#indexInfo"),
    fileInput: $("#fileInput"),
    citationList: $("#citationList"),
    ticketList: $("#ticketList"),
    inspector: $("#inspector"),
    sidebar: $("#sidebar"),
    scrim: $("#scrim"),
    toast: $("#toast"),
    status: $("#status"),
    statusText: $("#statusText"),
    menuBtn: $("#menuBtn"),
    closeInspector: $("#closeInspector"),
    refreshDocs: $("#refreshDocs"),
    refreshTickets: $("#refreshTickets"),
    sampleChips: $("#sampleChips"),
    empty: $("#empty"),
  };

  const state = {
    sessionId: localStorage.getItem("kbase_session") || null,
    citations: [],
    busy: false,
    lastBubble: null,
  };

  // ---------- 基础工具 ----------
  function toast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.classList.toggle("error", isError);
    els.toast.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => els.toast.classList.remove("show"), 2600);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (!res.ok) {
      const detail = data && data.detail ? (data.detail.message || data.detail) : res.statusText;
      throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    }
    return data;
  }

  function setStatus(text, kind = "") {
    els.statusText.textContent = text;
    const dot = els.status.querySelector(".dot");
    dot.className = `dot ${kind}`;
  }

  // ---------- 文档 ----------
  async function loadDocs() {
    try {
      const data = await api("/api/docs");
      renderDocs(data.items || []);
    } catch (err) {
      toast(`文档列表加载失败：${err.message}`, true);
    }
  }

  function renderDocs(items) {
    els.docCount.textContent = items.length;
    els.docList.textContent = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "还没有文档，先上传一份制度文件。";
      els.docList.append(li);
      return;
    }
    for (const doc of items) {
      const li = document.createElement("li");
      li.className = "doc";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = doc.title;
      name.title = doc.title;
      const badge = document.createElement("span");
      badge.className = `badge ${doc.status}`;
      badge.textContent = doc.status === "ready" ? `${doc.chunks} 块` : doc.status;
      li.append(name, badge);
      els.docList.append(li);
    }
  }

  async function uploadFile(file) {
    const form = new FormData();
    form.append("file", file);
    form.append("title", file.name.replace(/\.[^.]+$/, ""));
    try {
      toast("上传中…");
      const res = await api("/api/docs", { method: "POST", body: form });
      toast(res.deduped ? "已存在同一份文档，直接复用" : "已入队，正在解析与建索引");
      setTimeout(loadDocs, 600);
      setTimeout(loadDocs, 2200);
    } catch (err) {
      toast(`上传失败：${err.message}`, true);
    } finally {
      els.fileInput.value = "";
    }
  }

  // ---------- 对话 ----------
  function ensureEmptyRemoved() {
    if (els.empty && els.empty.parentNode) els.empty.remove();
  }

  function addMessage(role, text = "") {
    ensureEmptyRemoved();
    const wrap = document.createElement("article");
    wrap.className = `msg ${role}`;
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = role === "user" ? "我" : "KBase";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    wrap.append(who, bubble);
    els.messages.append(wrap);
    scrollToEnd();
    return { wrap, bubble };
  }

  function scrollToEnd() {
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function shortPath(c) {
    if (!c.path) return "";
    const p = c.path.startsWith(c.doc_title) ? c.path.slice(c.doc_title.length) : c.path;
    return p.replace(/^[\/·\s]+/, "");
  }

  function renderCitations(citations) {
    state.citations = citations || [];
    els.citationList.textContent = "";
    if (!state.citations.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "这一轮没有引用（拒答或办事类回答不会有引用）。";
      els.citationList.append(p);
      return;
    }
    for (const c of state.citations) {
      const div = document.createElement("div");
      div.className = "citation";
      div.id = `cite-${c.n}`;
      const head = document.createElement("div");
      head.className = "cite-head";
      const num = document.createElement("b");
      num.textContent = `[${c.n}]`;
      const tail = shortPath(c);
      const title = document.createElement("span");
      title.textContent = `${c.doc_title}${tail ? " · " + tail : ""} · 第 ${c.page} 页`;
      head.append(num, title);
      const p = document.createElement("p");
      p.textContent = c.snippet || "";
      div.append(head, p);
      els.citationList.append(div);
    }
  }

  function attachCiteChips(bubble, citations) {
    if (!citations || !citations.length) return;
    const row = document.createElement("div");
    row.className = "cites";
    for (const c of citations) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cite-chip";
      const tail = shortPath(c);
      btn.textContent = `[${c.n}] ${c.doc_title}${tail ? " · " + tail : ""}`;
      btn.title = `${c.doc_title} ${c.path} 第 ${c.page} 页`;
      btn.addEventListener("click", () => focusCitation(c.n));
      row.append(btn);
    }
    bubble.after(row);
  }

  function focusCitation(n) {
    openInspector();
    const el = document.getElementById(`cite-${n}`);
    if (!el) return;
    document.querySelectorAll(".citation.active").forEach((x) => x.classList.remove("active"));
    el.classList.add("active");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function parseBlock(block) {
    if (!block || block.startsWith(":")) return;
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    let data = {};
    try { data = JSON.parse(dataLines.join("\n")); } catch (e) { return; }
    handleEvent(event, data);
  }

  function handleEvent(event, data) {
    if (event === "meta") {
      state.sessionId = data.session_id || state.sessionId;
      if (state.sessionId) localStorage.setItem("kbase_session", state.sessionId);
      renderCitations(data.citations || []);
      if (data.insufficient) {
        state._insufficient = true;
      }
    } else if (event === "delta" && state.lastBubble) {
      state.lastBubble.bubble.textContent += data.text || "";
      scrollToEnd();
    } else if (event === "done") {
      if (!state.lastBubble) return;
      state.lastBubble.wrap.classList.remove("streaming");
      if (data.citations && data.citations.length) renderCitations(data.citations);
      attachCiteChips(state.lastBubble.bubble, data.citations || []);
      if (data.ticket) loadTickets();
      if (data.insufficient) {
        const note = document.createElement("div");
        note.className = "note";
        note.textContent = "没有找到足够依据，已按“不猜测”处理。";
        state.lastBubble.wrap.append(note);
      }
      if (data.latency_ms && data.latency_ms.total_ms) {
        setStatus(`就绪 · ${Math.round(data.latency_ms.total_ms)}ms`, "ok");
      }
    } else if (event === "error") {
      if (state.lastBubble) state.lastBubble.wrap.classList.remove("streaming");
      toast(data.message || "生成失败", true);
    }
  }

  async function sendMessage(text) {
    const message = (text || "").trim();
    if (!message || state.busy) return;
    state.busy = true;
    els.send.disabled = true;
    state._insufficient = false;
    addMessage("user", message);
    const bot = addMessage("bot");
    bot.wrap.classList.add("streaming");
    state.lastBubble = bot;
    if (els.input) { els.input.value = ""; els.input.style.height = "auto"; }

    let buffer = "";
    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: state.sessionId }),
      });
      if (!res.ok || !res.body) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          parseBlock(buffer.slice(0, idx));
          buffer = buffer.slice(idx + 2);
        }
      }
      if (buffer.trim()) parseBlock(buffer);
    } catch (err) {
      bot.wrap.classList.remove("streaming");
      toast(`请求失败：${err.message}`, true);
    } finally {
      state.busy = false;
      els.send.disabled = false;
      els.input.focus();
    }
  }

  // ---------- 工单 ----------
  async function loadTickets() {
    try {
      const data = await api("/api/tickets");
      els.ticketList.textContent = "";
      if (!data.items || !data.items.length) {
        const p = document.createElement("p");
        p.className = "muted";
        p.textContent = "还没有工单。";
        els.ticketList.append(p);
        return;
      }
      for (const t of data.items) {
        const div = document.createElement("div");
        div.className = "ticket";
        const b = document.createElement("b");
        b.textContent = `${t.id} · ${t.status}`;
        const row = document.createElement("div");
        row.className = "row";
        row.textContent = `${t.category} · ${t.urgency} · ${t.created_at.slice(0, 16).replace("T", " ")}`;
        const p = document.createElement("div");
        p.className = "row";
        p.textContent = t.summary;
        div.append(b, row, p);
        els.ticketList.append(div);
      }
    } catch (err) {
      // 工单面板失败不打断对话
      console.warn(err);
    }
  }

  // ---------- 抽屉与 sheet ----------
  function openSidebar() {
    els.sidebar.classList.add("open");
    els.scrim.hidden = false;
    els.menuBtn.setAttribute("aria-expanded", "true");
  }
  function openInspector() {
    if (window.matchMedia("(max-width: 1023px)").matches) {
      els.inspector.classList.add("open");
      els.scrim.hidden = false;
    }
  }
  function closeOverlays() {
    els.sidebar.classList.remove("open");
    els.inspector.classList.remove("open");
    els.scrim.hidden = true;
    els.menuBtn.setAttribute("aria-expanded", "false");
  }

  // ---------- 事件绑定 ----------
  els.menuBtn.addEventListener("click", openSidebar);
  els.scrim.addEventListener("click", closeOverlays);
  els.closeInspector.addEventListener("click", closeOverlays);
  els.refreshDocs.addEventListener("click", loadDocs);
  els.refreshTickets.addEventListener("click", loadTickets);

  els.fileInput.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) uploadFile(file);
  });

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(els.input.value);
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage(els.input.value);
    }
  });

  els.input.addEventListener("input", () => {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 140)}px`;
  });

  els.sampleChips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (btn) sendMessage(btn.textContent);
  });

  // ---------- 启动 ----------
  (async function init() {
    try {
      const health = await api("/api/health");
      els.indexInfo.textContent = `索引 ${health.index_chunks} 块 · ${health.documents} 份文档`;
      setStatus("就绪", "ok");
    } catch (err) {
      setStatus("服务不可用", "bad");
    }
    loadDocs();
    loadTickets();

    // 供截图脚本读取的横向溢出自检
    if (new URLSearchParams(location.search).has("audit")) {
      setTimeout(() => {
        const de = document.documentElement;
        const overflow = de.scrollWidth - window.innerWidth;
        document.title = `audit w=${window.innerWidth} sw=${de.scrollWidth} overflow=${overflow}`;
      }, 800);
    }

    // ?demo=1 时自动演示一轮问答，用于截图与录屏；不影响正常使用
    if (new URLSearchParams(location.search).get("demo") === "1") {
      setTimeout(() => sendMessage("一线城市出差住宿标准是多少？"), 300);
    }
  })();
})();
