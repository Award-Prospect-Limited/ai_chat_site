(function () {
  "use strict";

  const boot = JSON.parse(document.getElementById("bootData").textContent || "{}");
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (window.MD ? window.MD.escapeHtml(s) : String(s));

  const el = {
    sidebar: $("sidebar"),
    backdrop: $("sidebarBackdrop"),
    convList: $("conversationList"),
    convSearch: $("convSearch"),
    chatBox: $("chatBox"),
    chatMain: $("chatMain"),
    input: $("userInput"),
    send: $("btnSend"),
    stop: $("btnStop"),
    attach: $("btnAttach"),
    fileInput: $("fileInput"),
    fileList: $("fileList"),
    hint: $("hint"),
    charCount: $("charCount"),
    model: $("modelSelect"),
    thinking: $("thinkingSelect"),
    aspect: $("aspectSelect"),
    memory: $("memoryToggle"),
    title: $("currentConversationTitle"),
    persona: $("personaBadge"),
    dropOverlay: $("dropOverlay"),
    settingsDialog: $("settingsDialog"),
    settingsTitle: $("settingsTitle"),
    settingsPrompt: $("settingsPrompt"),
    personaGrid: $("personaGrid"),
    lightbox: $("lightbox"),
    lightboxImg: $("lightboxImg"),
    lightboxDl: $("lightboxDl"),
    ocrDialog: $("ocrDialog"),
    ocrName: $("ocrName"),
    ocrSub: $("ocrSub"),
    ocrPreview: $("ocrPreview"),
    ocrText: $("ocrText"),
  };

  const PERSONAS = [
    { name: "通用助手", prompt: "" },
    {
      name: "外贸业务",
      prompt:
        "你是一名经验丰富的外贸业务经理，熟悉询盘、报价、谈判、跟单、物流和国际贸易术语（FOB/CIF/DDP 等）。回答务实、给出可直接使用的话术或邮件；英文邮件要地道、礼貌、简洁。",
    },
    {
      name: "中英互译",
      prompt:
        "你是专业译者。用户输入中文就译成地道英文，输入英文就译成简体中文。只输出译文，保留原有格式与换行；专有名词不确定时在译文后用括号注明原文。",
    },
    {
      name: "编程助手",
      prompt:
        "你是资深软件工程师。先给出可运行的代码，再简要解释关键点；指出潜在的边界情况和安全问题；代码块标注语言。",
    },
    {
      name: "写作润色",
      prompt:
        "你是一位中文编辑。润色用户给出的文字：保持原意，使表达更通顺、准确、有文采；最后用列表简要说明改了哪些地方。",
    },
    {
      name: "数据分析",
      prompt:
        "你是数据分析师。面对表格或数据，先概括数据结构，再给出关键发现、异常值与建议；需要计算时写出计算过程，适合用表格展示的用 Markdown 表格。",
    },
    { name: "简洁模式", prompt: "回答尽量简短：先给结论，再给必要的理由；不要寒暄，不要重复问题。" },
  ];

  const STARTERS = [
    { title: "写一封外贸开发信", desc: "给潜在客户的第一封英文邮件", text: "帮我写一封英文外贸开发信。产品：\n目标客户：\n我们的优势：" },
    { title: "总结一份文档", desc: "上传 PDF / Word / Excel，提炼要点", text: "请总结这份文档的核心内容，列出关键数据和结论。", attach: true },
    { title: "联网查最新资讯", desc: "打开「联网」，回答附带来源", text: "今天有哪些值得关注的科技和外贸行业新闻？", tool: "search" },
    { title: "画一张插画", desc: "切换到绘图模型，文字生成图片", text: "一只在茶园里打盹的柴犬，清新水彩风格，柔和的晨光", image: true },
    { title: "图片文字识别（OCR）", desc: "上传截图、照片或扫描件，提取全部文字", ocr: true },
    { title: "看图说话", desc: "上传图片，让它描述、解读或找问题", text: "请详细描述这张图片，并指出值得注意的细节。", attach: true },
  ];

  const WAIT_TEXT = {
    chat: ["正在思考", "组织语言中", "翻阅笔记中", "马上就好"],
    search: ["正在搜索网页", "阅读搜索结果", "整理来源"],
    image: ["正在调色", "构图中", "落笔中", "晾干颜料"],
  };

  // ------------------------------------------------------------ 状态
  const models = boot.models || [];
  const modelById = Object.fromEntries(models.map((m) => [m.id, m]));
  let activeConversationId = null;
  let activeConversation = null;
  let conversations = [];
  let selectedFiles = [];
  let busy = false;
  let abortCtrl = null;
  let dragDepth = 0;
  let ocrAfterUpload = false;
  let ocrCurrent = null;

  const prefs = loadPrefs();

  function loadPrefs() {
    let p = {};
    try {
      p = JSON.parse(localStorage.getItem("chatPrefs") || "{}") || {};
    } catch (e) {}
    return {
      model: modelById[p.model] ? p.model : boot.defaultModel,
      thinking: typeof p.thinking === "string" ? p.thinking : "",
      tools: Array.isArray(p.tools) ? p.tools : [],
      memory: typeof p.memory === "boolean" ? p.memory : !!boot.memoryDefault,
      aspect: p.aspect || "1:1",
      lastConversation: p.lastConversation || null,
    };
  }
  function savePrefs() {
    try {
      localStorage.setItem("chatPrefs", JSON.stringify(prefs));
    } catch (e) {}
  }

  // ------------------------------------------------------------ 工具函数
  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  async function api(url, opts) {
    const o = Object.assign({ headers: {} }, opts || {});
    if (o.json !== undefined) {
      o.body = JSON.stringify(o.json);
      o.headers["Content-Type"] = "application/json";
      delete o.json;
    }
    o.headers["X-CSRFToken"] = csrf();
    const res = await fetch(url, o);
    let data = null;
    try {
      data = await res.json();
    } catch (e) {}
    if (!res.ok) throw new Error((data && data.error) || `请求失败（${res.status}）`);
    return data || {};
  }
  function fmtNum(n) {
    n = Number(n || 0);
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e4) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "k";
    return n.toLocaleString();
  }
  function fmtSize(b) {
    if (!b && b !== 0) return "";
    if (b < 1024) return b + " B";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
    return (b / 1024 / 1024).toFixed(1) + " MB";
  }
  function parseUtc(s) {
    if (!s) return null;
    const d = new Date(String(s).replace(" ", "T") + "Z");
    return isNaN(d) ? null : d;
  }
  function fmtTime(s) {
    const d = parseUtc(s);
    if (!d) return "";
    const now = new Date();
    const hm = d.toTimeString().slice(0, 5);
    if (d.toDateString() === now.toDateString()) return hm;
    return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  }
  function fileIcon(mime, name) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (mime === "application/pdf") return "fa-file-pdf";
    if (/^audio\//.test(mime)) return "fa-file-audio";
    if (/^video\//.test(mime)) return "fa-file-video";
    if (["xlsx", "csv"].includes(ext)) return "fa-file-excel";
    if (ext === "docx") return "fa-file-word";
    if (ext === "pptx") return "fa-file-powerpoint";
    if (["py", "js", "ts", "java", "go", "rs", "sql", "sh", "html", "xml", "json", "yaml", "yml"].includes(ext)) return "fa-file-code";
    return "fa-file-lines";
  }
  function canOcr(mime) {
    return /^image\//.test(mime || "") || mime === "application/pdf";
  }
  function setHint(text, isErr) {
    el.hint.textContent = text || "";
    el.hint.classList.toggle("err", !!isErr);
  }
  function currentModel() {
    return modelById[el.model.value] || models[0] || { id: boot.defaultModel, kind: "chat" };
  }
  function scrollToBottom(force) {
    const box = el.chatBox;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    if (force || nearBottom) box.scrollTop = box.scrollHeight;
  }

  // ------------------------------------------------------------ 模型与工具栏
  function buildModelSelect() {
    el.model.innerHTML = "";
    const groups = [
      ["对话模型", models.filter((m) => m.kind !== "image")],
      ["绘图模型", models.filter((m) => m.kind === "image")],
    ];
    for (const [label, list] of groups) {
      if (!list.length) continue;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const m of list) {
        const o = document.createElement("option");
        o.value = m.id;
        o.textContent = m.label;
        o.title = m.desc || "";
        og.appendChild(o);
      }
      el.model.appendChild(og);
    }
    el.model.value = prefs.model;
    if (!el.model.value && models[0]) el.model.value = models[0].id;

    el.aspect.innerHTML = "";
    for (const r of boot.aspectRatios || []) {
      const o = document.createElement("option");
      o.value = r;
      o.textContent = `比例 ${r}`;
      el.aspect.appendChild(o);
    }
    el.aspect.value = prefs.aspect;
    el.thinking.value = prefs.thinking;
    applyModeUi();
  }

  function applyModeUi() {
    const m = currentModel();
    const isImage = m.kind === "image";
    document.querySelectorAll(".chat-only").forEach((n) => n.classList.toggle("d-none", isImage || (!m.tools && n.dataset.tool)));
    document.querySelectorAll(".image-only").forEach((n) => n.classList.toggle("d-none", !isImage));
    if (m.thinking === "none") el.thinking.classList.add("d-none");
    document.querySelectorAll(".tool-btn.toggle[data-tool]").forEach((b) => b.classList.toggle("on", prefs.tools.includes(b.dataset.tool)));
    el.memory.classList.toggle("on", prefs.memory);
    el.thinking.classList.toggle("on", !!el.thinking.value);
    el.input.placeholder = isImage
      ? "描述你想要的画面；也可以上传图片，说明要怎么改"
      : "输入问题，Enter 发送，Shift+Enter 换行；可粘贴截图或拖入文件";
    el.model.title = m.desc || "";
  }

  // ------------------------------------------------------------ 对话列表
  function groupLabel(c) {
    if (c.pinned) return "置顶";
    const d = parseUtc(c.updated_at);
    if (!d) return "更早";
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const diff = (start - d) / 86400000;
    if (d >= start) return "今天";
    if (diff <= 1) return "昨天";
    if (diff <= 7) return "七天内";
    if (diff <= 30) return "本月";
    return "更早";
  }

  function renderConversationList() {
    el.convList.innerHTML = "";
    if (!conversations.length) {
      el.convList.innerHTML = `<div class="conv-empty">${el.convSearch.value ? "没有找到相关对话" : "还没有对话"}</div>`;
      return;
    }
    let lastGroup = null;
    for (const c of conversations) {
      const g = groupLabel(c);
      if (g !== lastGroup) {
        const h = document.createElement("div");
        h.className = "conv-group";
        h.textContent = g;
        el.convList.appendChild(h);
        lastGroup = g;
      }
      const item = document.createElement("div");
      item.className = "conv-item" + (c.id === activeConversationId ? " active" : "");
      item.dataset.id = c.id;
      item.innerHTML =
        `<span class="title">${esc(c.title || "新对话")}</span>` +
        (c.pinned ? '<i class="fa-solid fa-thumbtack pin"></i>' : "") +
        `<span class="actions">` +
        `<i class="fa-solid fa-thumbtack" data-act="pin" title="${c.pinned ? "取消置顶" : "置顶"}"></i>` +
        `<i class="fa-solid fa-pen" data-act="rename" title="重命名"></i>` +
        `<i class="fa-solid fa-trash-can del" data-act="delete" title="删除"></i></span>`;
      el.convList.appendChild(item);
    }
  }

  async function fetchConversations() {
    const q = el.convSearch.value.trim();
    const data = await api("/api/conversations" + (q ? `?q=${encodeURIComponent(q)}` : ""));
    conversations = data.conversations || [];
    renderConversationList();
  }

  async function selectConversation(id) {
    if (busy) return;
    activeConversationId = Number(id);
    prefs.lastConversation = activeConversationId;
    savePrefs();
    renderConversationList();
    closeSidebar();
    await loadActiveConversation();
  }

  async function newConversation(opts) {
    if (busy) return;
    const data = await api("/api/conversations", { method: "POST", json: { title: "新对话", ...(opts || {}) } });
    el.convSearch.value = "";
    await fetchConversations();
    await selectConversation(data.id);
    el.input.focus();
  }

  async function renameConversation(id, current) {
    const title = (window.prompt("新的对话标题", current || "") || "").trim();
    if (!title) return;
    await api(`/api/conversations/${id}`, { method: "PATCH", json: { title } });
    await fetchConversations();
    if (Number(id) === activeConversationId) el.title.textContent = title;
  }

  async function deleteConversation(id) {
    if (!window.confirm("删除后无法恢复，确定删除这个对话吗？")) return;
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    await fetchConversations();
    if (Number(id) === activeConversationId) {
      if (conversations.length) await selectConversation(conversations[0].id);
      else await newConversation();
    }
  }

  async function togglePin(id) {
    const c = conversations.find((x) => x.id === Number(id));
    await api(`/api/conversations/${id}`, { method: "PATCH", json: { pinned: !(c && c.pinned) } });
    await fetchConversations();
  }

  // ------------------------------------------------------------ 消息渲染
  function showWelcome() {
    el.chatBox.innerHTML = "";
    const w = document.createElement("div");
    w.className = "welcome";
    const hour = new Date().getHours();
    const greet = hour < 6 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
    w.innerHTML =
      `<span class="leaf lg"></span>` +
      `<h2>${greet}，<span>${esc(boot.username || "")}</span></h2>` +
      `<p>今天想聊点什么？可以提问、上传文件，也可以让它帮你画一张图。</p>` +
      `<div class="starter-grid">${STARTERS.map(
        (s, i) =>
          `<div class="starter" data-i="${i}"><span class="no">${String(i + 1).padStart(2, "0")}</span><div><b>${esc(s.title)}</b><span>${esc(
            s.desc
          )}</span></div></div>`
      ).join("")}</div>`;
    el.chatBox.appendChild(w);
  }

  function useStarter(i) {
    const s = STARTERS[i];
    if (!s) return;
    if (s.image) {
      const img = models.find((m) => m.kind === "image");
      if (img) {
        el.model.value = img.id;
        prefs.model = img.id;
      }
    } else if (currentModel().kind === "image") {
      el.model.value = boot.defaultModel;
      prefs.model = boot.defaultModel;
    }
    if (s.tool && !prefs.tools.includes(s.tool)) prefs.tools.push(s.tool);
    savePrefs();
    applyModeUi();
    if (s.ocr) {
      ocrAfterUpload = true;
      el.fileInput.accept = "image/*,.pdf";
      el.fileInput.click();
      return;
    }
    el.input.value = s.text;
    autosize();
    el.input.focus();
    if (s.attach) el.fileInput.click();
  }

  function attachmentsHtml(list, align) {
    if (!list || !list.length) return "";
    const imgs = list.filter((a) => a.is_image);
    const others = list.filter((a) => !a.is_image);
    return (
      `<div class="attach-row" style="justify-content:${align}">` +
      imgs
        .map(
          (a) =>
            `<span class="thumb-wrap"><img class="attach-thumb" src="${a.url}" data-full="${a.url}" alt="${esc(a.name)}" loading="lazy" />` +
            (a.id ? `<button class="ocr-btn" type="button" data-ocr="${a.id}" data-name="${esc(a.name)}" data-mime="${esc(a.mime || "")}">识别文字</button>` : "") +
            `</span>`
        )
        .join("") +
      others
        .map(
          (a) =>
            `<span class="attach-chip"><a class="d-inline-flex align-items-center gap-2 text-reset text-decoration-none" style="min-width:0" href="${a.url}?download=1" title="${esc(a.name)}"><i class="fa-solid ${fileIcon(a.mime, a.name)}"></i><span>${esc(
              a.name
            )}</span></a>` +
            (a.id && canOcr(a.mime) ? `<button class="ocr-btn" type="button" data-ocr="${a.id}" data-name="${esc(a.name)}" data-mime="${esc(a.mime || "")}">OCR</button>` : "") +
            `</span>`
        )
        .join("") +
      `</div>`
    );
  }

  function renderUser(m) {
    const row = document.createElement("div");
    row.className = "msg-row user";
    row.dataset.id = m.id || "";
    row._msg = m;
    let content = m.content || "";
    // 兼容旧数据：附件名曾拼在正文末尾
    content = content.replace(/\n\[附件\] .*$/, "");
    row.innerHTML =
      attachmentsHtml(m.attachments, "flex-end") +
      `<div class="note"></div>` +
      `<div class="msg-actions">` +
      `<span class="msg-meta">${fmtTime(m.created_at)}</span>` +
      `<button type="button" data-act="copy" title="复制"><i class="fa-regular fa-copy"></i></button>` +
      `<button type="button" data-act="edit" title="编辑后重新发送"><i class="fa-solid fa-pen"></i></button>` +
      `</div>`;
    row.querySelector(".note").textContent = content;
    row._text = content;
    return row;
  }

  function aiSkeleton(modelId) {
    const row = document.createElement("div");
    row.className = "msg-row ai";
    const m = modelById[modelId];
    row.innerHTML =
      `<div class="answer">` +
      `<div class="answer-head"><span class="leaf"></span><span class="model-tag">${esc(m ? m.label : modelId || "Gemini")}</span><span class="stamps"></span></div>` +
      `<details class="thinking d-none"><summary><i class="fa-solid fa-chevron-right chev"></i><span class="label">思考过程</span></summary><div class="thinking-body md"></div></details>` +
      `<div class="gen-images d-none"></div>` +
      `<div class="md body"></div>` +
      `<div class="sources-wrap"></div>` +
      `<div class="msg-actions">` +
      `<button type="button" data-act="copy" title="复制"><i class="fa-regular fa-copy"></i> 复制</button>` +
      `<button type="button" data-act="regen" title="重新生成"><i class="fa-solid fa-rotate-right"></i> 重新生成</button>` +
      `<span class="msg-meta"></span>` +
      `</div></div>`;
    row._text = "";
    row._thought = "";
    return row;
  }

  function setAiStamps(row, meta) {
    const tags = [];
    if (meta && meta.thinking) tags.push({ low: "快速", medium: "标准", high: "深度" }[meta.thinking] || meta.thinking);
    for (const t of (meta && meta.tools) || []) tags.push({ search: "联网", url: "读网页", code: "代码" }[t] || t);
    if (meta && meta.aspect_ratio) tags.push(meta.aspect_ratio);
    if (meta && meta.memory_used) tags.push("记忆");
    row.querySelector(".stamps").innerHTML = tags.map((t) => `<span class="stamp">${esc(t)}</span>`).join(" ");
  }

  function setAiMeta(row, m) {
    const parts = [];
    if (m.created_at) parts.push(fmtTime(m.created_at));
    if (m.total_tokens) parts.push(`${fmtNum(m.total_tokens)} tok`);
    if (m.meta && m.meta.elapsed_ms) parts.push(`${(m.meta.elapsed_ms / 1000).toFixed(1)}s`);
    if (m.meta && m.meta.stopped) parts.push("已停止");
    row.querySelector(".msg-meta").textContent = parts.join(" · ");
  }

  function setThinking(row, text, live) {
    const box = row.querySelector(".thinking");
    if (!text) return;
    box.classList.remove("d-none");
    box.classList.toggle("live", !!live);
    box.querySelector(".label").textContent = live ? "正在思考" : "思考过程";
    window.MD.render(box.querySelector(".thinking-body"), text, { final: !live });
    if (live) {
      const b = box.querySelector(".thinking-body");
      b.scrollTop = b.scrollHeight;
    }
  }

  function addImage(row, file) {
    const wrap = row.querySelector(".gen-images");
    wrap.classList.remove("d-none");
    const fig = document.createElement("figure");
    fig.innerHTML = `<img src="${file.url}" data-full="${file.url}" alt="生成的图片" loading="lazy" /><figcaption><span>#${file.id}</span><a href="${file.url}?download=1" title="下载"><i class="fa-solid fa-download"></i></a></figcaption>`;
    wrap.appendChild(fig);
  }

  function setSources(row, g) {
    const wrap = row.querySelector(".sources-wrap");
    if (!g || (!g.sources?.length && !g.queries?.length)) {
      wrap.innerHTML = "";
      return;
    }
    let html = "";
    if (g.sources && g.sources.length) {
      html +=
        `<div class="sources"><div class="label">参考来源</div><ol>` +
        g.sources
          .map((s, i) => `<li><span class="n">[${i + 1}]</span><a href="${esc(s.uri)}" target="_blank" rel="noopener noreferrer nofollow" title="${esc(s.title)}">${esc(s.title)}</a></li>`)
          .join("") +
        `</ol>`;
      if (g.queries && g.queries.length) html += `<div class="search-queries">搜索词：${g.queries.map((q) => `<code>${esc(q)}</code>`).join("")}</div>`;
      html += `</div>`;
    }
    wrap.innerHTML = html;
  }

  function renderAi(m) {
    const row = aiSkeleton(m.model_name);
    row.dataset.id = m.id || "";
    row._text = m.content || "";
    const body = row.querySelector(".body");
    if (m.meta && m.meta.error && (m.content || "").startsWith("⚠️")) {
      body.innerHTML = `<div class="error-note">${esc(m.content)}</div>`;
    } else {
      window.MD.render(body, m.content || "");
    }
    if (m.thoughts) setThinking(row, m.thoughts, false);
    for (const a of m.attachments || []) addImage(row, a);
    setSources(row, m.grounding);
    setAiStamps(row, m.meta);
    setAiMeta(row, m);
    return row;
  }

  function markLast() {
    const rows = el.chatBox.querySelectorAll(".msg-row");
    rows.forEach((r) => r.classList.remove("last"));
    const aiRows = el.chatBox.querySelectorAll(".msg-row.ai");
    aiRows.forEach((r) => {
      const b = r.querySelector('[data-act="regen"]');
      if (b) b.classList.add("d-none");
    });
    const lastRow = rows[rows.length - 1];
    if (lastRow) {
      lastRow.classList.add("last");
      const b = lastRow.querySelector('[data-act="regen"]');
      if (b) b.classList.remove("d-none");
    }
  }

  async function loadActiveConversation() {
    const [conv, msgs] = await Promise.all([
      api(`/api/conversations/${activeConversationId}`),
      api(`/api/conversations/${activeConversationId}/messages`),
    ]);
    activeConversation = conv;
    el.title.textContent = conv.title || "新对话";
    updatePersonaBadge();
    el.chatBox.innerHTML = "";
    const list = msgs.messages || [];
    if (!list.length) showWelcome();
    for (const m of list) el.chatBox.appendChild(m.role === "user" ? renderUser(m) : renderAi(m));
    markLast();
    requestAnimationFrame(() => scrollToBottom(true));
    refreshStats();
  }

  function updatePersonaBadge() {
    const p = (activeConversation && activeConversation.system_prompt) || "";
    const hit = PERSONAS.find((x) => x.prompt && x.prompt === p);
    el.persona.classList.toggle("d-none", !p);
    el.persona.textContent = hit ? hit.name : "自定义人设";
    el.persona.title = p;
  }

  async function refreshStats() {
    try {
      setStats(await api(`/api/stats?conversation_id=${activeConversationId}`));
    } catch (e) {}
  }
  function setStats(s) {
    if (!s) return;
    $("tokChat").textContent = fmtNum(s.current_chat_tokens);
    $("tokWeek").textContent = fmtNum(s.week_tokens);
    $("tokMonth").textContent = fmtNum(s.month_tokens);
    $("tokTotal").textContent = fmtNum(s.total_tokens);
  }

  // ------------------------------------------------------------ 发送（流式）
  function setBusy(b) {
    busy = b;
    el.send.classList.toggle("d-none", b);
    el.stop.classList.toggle("d-none", !b);
    el.model.disabled = b;
  }

  function waitIndicator(row, kind) {
    const body = row.querySelector(".body");
    const words = WAIT_TEXT[kind] || WAIT_TEXT.chat;
    body.innerHTML = `<div class="growing"><span class="sprout"></span><span class="sprout"></span><span class="sprout"></span><span class="wtxt">${words[0]}</span></div>`;
    let i = 0;
    const t = setInterval(() => {
      const w = body.querySelector(".wtxt");
      if (!w) return clearInterval(t);
      i = (i + 1) % words.length;
      w.textContent = words[i];
    }, 2200);
    return () => clearInterval(t);
  }

  async function send(opts) {
    opts = opts || {};
    if (busy) return;
    const m = currentModel();
    const isRegen = !!opts.regenerate;
    const text = isRegen ? "" : (opts.message !== undefined ? opts.message : el.input.value).trim();
    const files = opts.files || (isRegen ? [] : selectedFiles.filter((f) => f.id));
    if (!isRegen && !text && !files.length) return;
    if (selectedFiles.some((f) => f.uploading) && !opts.files) {
      setHint("文件还在上传，请稍等…");
      return;
    }
    if (text.length > (boot.maxMessageChars || 32000)) {
      setHint(`内容过长（最多 ${boot.maxMessageChars} 字）`, true);
      return;
    }

    const welcome = el.chatBox.querySelector(".welcome");
    if (welcome) welcome.remove();

    let userRow = null;
    if (isRegen) {
      const last = el.chatBox.lastElementChild;
      if (last && last.classList.contains("ai")) last.remove();
      const users = el.chatBox.querySelectorAll(".msg-row.user");
      userRow = users[users.length - 1] || null;
    } else {
      if (opts.truncateFrom) {
        // 编辑：移除该消息及之后的所有 DOM
        let n = el.chatBox.querySelector(`.msg-row[data-id="${opts.truncateFrom}"]`);
        while (n) {
          const next = n.nextElementSibling;
          n.remove();
          n = next;
        }
      }
      userRow = renderUser({ content: text, attachments: files.map((f) => ({ ...f, url: f.url || f.previewUrl })), created_at: null });
      el.chatBox.appendChild(userRow);
      if (!opts.message) {
        el.input.value = "";
        autosize();
      }
      if (!opts.files) {
        selectedFiles = [];
        renderFiles();
      }
    }

    const aiRow = aiSkeleton(m.id);
    el.chatBox.appendChild(aiRow);
    markLast();
    scrollToBottom(true);
    const stopWait = waitIndicator(aiRow, m.kind === "image" ? "image" : prefs.tools.includes("search") ? "search" : "chat");

    setBusy(true);
    setHint("");
    abortCtrl = new AbortController();

    const payload = {
      conversation_id: activeConversationId,
      message: text,
      file_ids: files.map((f) => f.id),
      model: m.id,
      thinking: el.thinking.value || null,
      tools: m.kind === "image" ? [] : prefs.tools,
      memory_enabled: prefs.memory,
      aspect_ratio: m.kind === "image" ? el.aspect.value : null,
      regenerate: isRegen,
      truncate_from: opts.truncateFrom || null,
    };

    let text_ = "";
    let thought = "";
    let renderPending = false;
    let gotContent = false;
    const body = aiRow.querySelector(".body");

    const scheduleRender = () => {
      if (renderPending) return;
      renderPending = true;
      requestAnimationFrame(() => {
        renderPending = false;
        if (thought) setThinking(aiRow, thought, !text_);
        if (text_) {
          window.MD.render(body, text_, { final: false });
          body.classList.add("typing-cursor");
        }
        scrollToBottom(false);
      });
    };

    const handle = (ev) => {
      switch (ev.type) {
        case "start":
          if (userRow && ev.user_message_id) {
            userRow.dataset.id = ev.user_message_id;
            userRow._msg = { id: ev.user_message_id, content: userRow._text, attachments: files };
          }
          break;
        case "model":
          if (ev.id !== m.id) {
            aiRow.querySelector(".model-tag").textContent = (modelById[ev.id] || {}).label || ev.id;
            setHint(`所选模型暂不可用，已自动切换到 ${ev.id}`);
          }
          break;
        case "thought":
          thought += ev.text;
          aiRow._thought = thought;
          if (!gotContent) {
            stopWait();
            body.innerHTML = "";
          }
          scheduleRender();
          break;
        case "text":
          if (!gotContent) {
            gotContent = true;
            stopWait();
          }
          text_ += ev.text;
          aiRow._text = text_;
          scheduleRender();
          break;
        case "image":
          if (!gotContent) {
            gotContent = true;
            stopWait();
            body.innerHTML = "";
          }
          addImage(aiRow, ev.file);
          scrollToBottom(true);
          break;
        case "grounding":
          setSources(aiRow, ev);
          break;
        case "error":
          stopWait();
          body.innerHTML = `<div class="error-note">⚠️ ${esc(ev.message)}</div>`;
          aiRow._text = "";
          break;
        case "done":
          aiRow.dataset.id = ev.message_id;
          aiRow.querySelector(".model-tag").textContent = (modelById[ev.model] || {}).label || ev.model;
          setAiStamps(aiRow, ev.meta);
          setAiMeta(aiRow, { created_at: new Date().toISOString().slice(0, 19).replace("T", " "), total_tokens: ev.usage && ev.usage.total, meta: ev.meta });
          setStats(ev.stats);
          break;
        case "title":
          el.title.textContent = ev.title;
          fetchConversations();
          break;
      }
    };

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify(payload),
        signal: abortCtrl.signal,
      });
      if (!res.ok || !res.body) {
        let msg = `请求失败（${res.status}）`;
        try {
          msg = (await res.json()).error || msg;
        } catch (e) {
          if (res.status === 429) msg = "发送太频繁，请稍后再试";
        }
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              handle(JSON.parse(line.slice(6)));
            } catch (e) {
              console.warn("bad event", e);
            }
          }
        }
      }
    } catch (e) {
      stopWait();
      if (e.name === "AbortError") {
        setHint("已停止生成");
        if (!text_ && !aiRow.querySelector(".gen-images img")) body.innerHTML = '<span class="text-muted small">（已停止）</span>';
        // 服务端会保存已生成的部分；稍后同步消息 ID，以便编辑 / 重新生成
        setTimeout(() => loadActiveConversation().catch(() => {}), 800);
      } else {
        body.innerHTML = `<div class="error-note">⚠️ ${esc(e.message || "网络异常")}</div>`;
      }
    } finally {
      stopWait();
      body.classList.remove("typing-cursor");
      if (text_) window.MD.render(body, text_, { final: true });
      if (thought) setThinking(aiRow, thought, false);
      if (!text_ && thought && !aiRow.querySelector(".gen-images img") && !body.querySelector(".error-note")) body.innerHTML = "";
      setBusy(false);
      abortCtrl = null;
      markLast();
      scrollToBottom(false);
      el.input.focus();
    }
  }

  function stopGenerating() {
    if (abortCtrl) abortCtrl.abort();
  }

  function startEdit(row) {
    if (busy) return;
    const msg = row._msg || {};
    const id = row.dataset.id;
    if (!id) return;
    const note = row.querySelector(".note");
    const actions = row.querySelector(".msg-actions");
    note.classList.add("d-none");
    actions.classList.add("d-none");
    const box = document.createElement("div");
    box.className = "edit-box";
    box.innerHTML =
      `<textarea rows="4"></textarea><div class="d-flex justify-content-end gap-2 mt-2">` +
      `<button class="btn btn-sm btn-ghost" data-e="cancel" type="button">取消</button>` +
      `<button class="btn btn-sm btn-accent" data-e="ok" type="button">发送</button></div>`;
    const ta = box.querySelector("textarea");
    ta.value = row._text || "";
    row.appendChild(box);
    ta.focus();
    box.addEventListener("click", (e) => {
      const b = e.target.closest("[data-e]");
      if (!b) return;
      if (b.dataset.e === "cancel") {
        box.remove();
        note.classList.remove("d-none");
        actions.classList.remove("d-none");
        return;
      }
      const text = ta.value.trim();
      if (!text) return;
      send({ message: text, truncateFrom: Number(id), files: (msg.attachments || []).map((a) => ({ ...a })) });
    });
  }

  // ------------------------------------------------------------ 附件
  function renderFiles() {
    el.fileList.innerHTML = "";
    selectedFiles.forEach((f, i) => {
      const chip = document.createElement("div");
      chip.className = "file-chip" + (f.uploading ? " uploading" : "");
      const icon = f.is_image && f.previewUrl ? `<img class="thumb" src="${f.previewUrl}" alt="" />` : `<span class="ficon"><i class="fa-solid ${fileIcon(f.mime || "", f.name)}"></i></span>`;
      chip.innerHTML = `${icon}<span class="info"><span class="name" title="${esc(f.name)}">${esc(f.name)}</span><span class="meta">${
        f.uploading ? "上传中…" : fmtSize(f.size)
      }</span></span>${f.id && canOcr(f.mime) ? '<button class="ocr-btn" type="button" title="识别文字">OCR</button>' : ""}<button class="remove" type="button" title="移除">×</button>`;
      const ob = chip.querySelector(".ocr-btn");
      if (ob) ob.addEventListener("click", () => openOcr({ id: f.id, name: f.name, mime: f.mime, url: f.previewUrl || f.url }));
      chip.querySelector(".remove").addEventListener("click", async () => {
        selectedFiles.splice(i, 1);
        renderFiles();
        if (f.id) fetch(`/api/upload/${f.id}`, { method: "DELETE", headers: { "X-CSRFToken": csrf() } }).catch(() => {});
      });
      el.fileList.appendChild(chip);
    });
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    const allowed = new Set((boot.allowedExt || []).map((x) => x.toLowerCase()));
    for (const file of files) {
      if (selectedFiles.length >= (boot.maxFiles || 10)) {
        setHint(`一次最多附带 ${boot.maxFiles} 个文件`, true);
        break;
      }
      let name = file.name || "";
      if (!name || name === "image.png") name = `粘贴图片-${Date.now()}.${(file.type.split("/")[1] || "png").replace("jpeg", "jpg")}`;
      const ext = (name.split(".").pop() || "").toLowerCase();
      if (allowed.size && !allowed.has(ext)) {
        setHint(`不支持 .${ext} 文件`, true);
        continue;
      }
      if (boot.maxUploadBytes && file.size > boot.maxUploadBytes) {
        setHint(`${name} 超过 ${fmtSize(boot.maxUploadBytes)}`, true);
        continue;
      }
      const isImg = /^image\//.test(file.type);
      const entry = { name, size: file.size, mime: file.type, is_image: isImg, uploading: true, previewUrl: isImg ? URL.createObjectURL(file) : null };
      selectedFiles.push(entry);
      renderFiles();
      const form = new FormData();
      form.append("file", file, name);
      try {
        const res = await fetch("/api/upload", { method: "POST", body: form, headers: { "X-CSRFToken": csrf() } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "上传失败");
        Object.assign(entry, data, { uploading: false, url: `/api/files/${data.id}` });
        setHint("");
        if (ocrAfterUpload && canOcr(entry.mime)) {
          ocrAfterUpload = false;
          openOcr({ id: entry.id, name: entry.name, mime: entry.mime, url: entry.previewUrl || entry.url });
        }
      } catch (e) {
        selectedFiles = selectedFiles.filter((x) => x !== entry);
        setHint(`${name}：${e.message}`, true);
      }
      renderFiles();
    }
  }

  // ------------------------------------------------------------ 设置 / 人设
  function openSettings() {
    if (!activeConversation) return;
    el.settingsTitle.value = activeConversation.title || "";
    el.settingsPrompt.value = activeConversation.system_prompt || "";
    renderPersonaGrid();
    el.settingsDialog.showModal();
  }
  function renderPersonaGrid() {
    const cur = el.settingsPrompt.value.trim();
    el.personaGrid.innerHTML = "";
    PERSONAS.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = p.name;
      b.classList.toggle("on", p.prompt === cur);
      b.addEventListener("click", () => {
        el.settingsPrompt.value = p.prompt;
        renderPersonaGrid();
      });
      el.personaGrid.appendChild(b);
    });
  }
  async function saveSettings() {
    const title = el.settingsTitle.value.trim() || activeConversation.title;
    const system_prompt = el.settingsPrompt.value.trim();
    await api(`/api/conversations/${activeConversationId}`, { method: "PATCH", json: { title, system_prompt } });
    activeConversation.title = title;
    activeConversation.system_prompt = system_prompt;
    el.title.textContent = title;
    updatePersonaBadge();
    fetchConversations();
    setHint(system_prompt ? "人设已保存，本对话之后的回答都会遵循" : "已清除人设");
  }

  // ------------------------------------------------------------ OCR
  async function openOcr(file, force) {
    ocrCurrent = file;
    el.ocrName.textContent = file.name || "";
    el.ocrText.value = "";
    el.ocrSub.textContent = "正在识别，图片通常几秒，多页 PDF 会久一些…";
    el.ocrPreview.innerHTML = /^image\//.test(file.mime || "")
      ? `<img src="${file.url || `/api/files/${file.id}`}" alt="" />`
      : `<div class="ficon"><i class="fa-solid ${fileIcon(file.mime || "", file.name || "")}"></i><p>${esc(file.name || "")}</p></div>`;
    if (!el.ocrDialog.open) el.ocrDialog.showModal();
    const t0 = performance.now();
    try {
      const r = await api(`/api/files/${file.id}/ocr${force ? "?force=1" : ""}`, { method: "POST" });
      if (ocrCurrent !== file) return;
      el.ocrText.value = r.text || "";
      const chars = (r.text || "").replace(/\s/g, "").length;
      el.ocrSub.textContent = r.cached
        ? `已识别过（缓存结果），共 ${chars} 字；可直接编辑后复制`
        : `识别完成：${chars} 字 · ${((performance.now() - t0) / 1000).toFixed(1)}s${r.tokens ? ` · ${fmtNum(r.tokens)} tok` : ""}；可直接编辑后复制`;
    } catch (e) {
      if (ocrCurrent === file) el.ocrSub.textContent = `识别失败：${e.message}`;
    }
  }

  // ------------------------------------------------------------ 其他交互
  function autosize() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, window.innerHeight * 0.32) + "px";
    const n = el.input.value.length;
    el.charCount.textContent = n > 500 ? `${n.toLocaleString()} 字` : "";
  }
  function openSidebar() {
    el.sidebar.classList.add("open");
    el.backdrop.classList.add("open");
  }
  function closeSidebar() {
    el.sidebar.classList.remove("open");
    el.backdrop.classList.remove("open");
  }
  function syncHljsTheme() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const l = $("hljsLight");
    const d = $("hljsDark");
    if (l) l.disabled = dark;
    if (d) d.disabled = !dark;
  }
  function openLightbox(src) {
    el.lightboxImg.src = src;
    el.lightboxDl.href = src + "?download=1";
    el.lightbox.showModal();
  }

  function bind() {
    el.send.addEventListener("click", () => send());
    el.stop.addEventListener("click", stopGenerating);
    el.input.addEventListener("input", autosize);
    el.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        send();
      }
    });
    el.input.addEventListener("paste", (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
      if (files.length) {
        e.preventDefault();
        uploadFiles(files);
      }
    });

    el.attach.addEventListener("click", () => el.fileInput.click());
    el.fileInput.addEventListener("change", async () => {
      await uploadFiles(el.fileInput.files);
      el.fileInput.value = "";
      el.fileInput.accept = "";
      ocrAfterUpload = false;
    });

    $("ocrClose").addEventListener("click", () => el.ocrDialog.close());
    $("ocrRetry").addEventListener("click", () => ocrCurrent && openOcr(ocrCurrent, true));
    $("ocrCopy").addEventListener("click", () => window.MD.copyText(el.ocrText.value).then(() => (el.ocrSub.textContent = "已复制到剪贴板")));
    $("ocrInsert").addEventListener("click", () => {
      const t = el.ocrText.value.trim();
      if (!t) return;
      el.input.value = (el.input.value ? el.input.value + "\n\n" : "") + t;
      autosize();
      el.ocrDialog.close();
      el.input.focus();
    });

    document.addEventListener("dragenter", (e) => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
      e.preventDefault();
      dragDepth++;
      el.dropOverlay.classList.remove("d-none");
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) el.dropOverlay.classList.add("d-none");
    });
    document.addEventListener("drop", (e) => {
      e.preventDefault();
      dragDepth = 0;
      el.dropOverlay.classList.add("d-none");
      if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
    });

    el.model.addEventListener("change", () => {
      prefs.model = el.model.value;
      savePrefs();
      applyModeUi();
    });
    el.thinking.addEventListener("change", () => {
      prefs.thinking = el.thinking.value;
      savePrefs();
      applyModeUi();
    });
    el.aspect.addEventListener("change", () => {
      prefs.aspect = el.aspect.value;
      savePrefs();
    });
    document.querySelectorAll(".tool-btn.toggle[data-tool]").forEach((b) =>
      b.addEventListener("click", () => {
        const t = b.dataset.tool;
        prefs.tools = prefs.tools.includes(t) ? prefs.tools.filter((x) => x !== t) : prefs.tools.concat(t);
        savePrefs();
        applyModeUi();
      })
    );
    el.memory.addEventListener("click", () => {
      prefs.memory = !prefs.memory;
      savePrefs();
      applyModeUi();
      setHint(prefs.memory ? "记忆已开启：会参考你过往对话中的相关内容" : "记忆已关闭");
    });

    $("btnNewConversation").addEventListener("click", () => newConversation().catch((e) => setHint(e.message, true)));
    let searchTimer = null;
    el.convSearch.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => fetchConversations().catch(() => {}), 250);
    });
    el.convList.addEventListener("click", (e) => {
      const item = e.target.closest(".conv-item");
      if (!item) return;
      const id = Number(item.dataset.id);
      const act = e.target.closest("[data-act]");
      const run = (p) => p.catch((err) => setHint(err.message, true));
      if (act) {
        e.stopPropagation();
        const c = conversations.find((x) => x.id === id);
        if (act.dataset.act === "rename") run(renameConversation(id, c && c.title));
        else if (act.dataset.act === "delete") run(deleteConversation(id));
        else if (act.dataset.act === "pin") run(togglePin(id));
        return;
      }
      if (id !== activeConversationId) run(selectConversation(id));
      else closeSidebar();
    });

    el.title.addEventListener("click", () => renameConversation(activeConversationId, el.title.textContent).catch((e) => setHint(e.message, true)));
    $("btnSettings").addEventListener("click", openSettings);
    el.settingsPrompt.addEventListener("input", renderPersonaGrid);
    el.settingsDialog.addEventListener("close", () => {
      if (el.settingsDialog.returnValue === "save") saveSettings().catch((e) => setHint(e.message, true));
    });
    $("btnExport").addEventListener("click", () => {
      window.location.href = `/api/conversations/${activeConversationId}/export`;
    });
    $("btnClear").addEventListener("click", async () => {
      if (busy || !window.confirm("清空本对话的全部消息？")) return;
      try {
        await api("/api/chat/clear", { method: "POST", json: { conversation_id: activeConversationId } });
        showWelcome();
        refreshStats();
      } catch (e) {
        setHint(e.message, true);
      }
    });

    el.chatBox.addEventListener("click", (e) => {
      const starter = e.target.closest(".starter");
      if (starter) return useStarter(Number(starter.dataset.i));
      const ocr = e.target.closest("[data-ocr]");
      if (ocr) return openOcr({ id: Number(ocr.dataset.ocr), name: ocr.dataset.name, mime: ocr.dataset.mime });
      const img = e.target.closest("img[data-full]");
      if (img) return openLightbox(img.dataset.full);
      const act = e.target.closest("[data-act]");
      if (!act) return;
      const row = act.closest(".msg-row");
      if (act.dataset.act === "copy") {
        window.MD.copyText(row._text || "").then(() => setHint("已复制"));
      } else if (act.dataset.act === "edit") {
        startEdit(row);
      } else if (act.dataset.act === "regen") {
        send({ regenerate: true });
      }
    });

    $("btnSidebar") && $("btnSidebar").addEventListener("click", openSidebar);
    el.backdrop.addEventListener("click", closeSidebar);
    document.addEventListener("themechange", syncHljsTheme);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && busy) stopGenerating();
    });
  }

  // ------------------------------------------------------------ 启动
  async function init() {
    syncHljsTheme();
    buildModelSelect();
    bind();
    autosize();
    try {
      await fetchConversations();
      const remembered = conversations.find((c) => c.id === prefs.lastConversation);
      const first = remembered || conversations[0];
      if (first) await selectConversation(first.id);
      else await newConversation();
    } catch (e) {
      setHint(e.message || "加载失败", true);
    }
    el.input.focus();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
