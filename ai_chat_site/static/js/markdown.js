/* Markdown 渲染：marked + DOMPurify（防 XSS）+ KaTeX（公式）+ highlight.js（代码高亮）
 * 暴露 window.MD.render(el, text, {final}) */
(function () {
  // 私有区字符做占位符，marked 不会转义它们
  const PH_OPEN = "";
  const PH_CLOSE = "";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderMath(tex, display) {
    if (!window.katex) return escapeHtml(display ? `$$${tex}$$` : `$${tex}$`);
    try {
      return window.katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: "ignore", trust: false });
    } catch (e) {
      return escapeHtml(tex);
    }
  }

  // 先藏起代码，再把公式换成占位符，避免 marked 把 _ \ * 当成 Markdown 语法
  function protectMath(src) {
    const codes = [];
    let s = src.replace(/(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g, (m) => {
      codes.push(m);
      return `${PH_OPEN}C${codes.length - 1}${PH_CLOSE}`;
    });
    const maths = [];
    const push = (html) => {
      maths.push(html);
      return `${PH_OPEN}M${maths.length - 1}${PH_CLOSE}`;
    };
    s = s
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => push(renderMath(t.trim(), true)))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => push(renderMath(t.trim(), true)))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => push(renderMath(t.trim(), false)))
      // 行内 $...$：两侧不能是空格，结尾 $ 后不能紧跟数字（避免把 "$5 和 $10" 当公式）
      .replace(/(^|[^\\$\w])\$([^\s$](?:[^$\n]*?[^\s$\\])?)\$(?![\w$])/g, (m, pre, t) => pre + push(renderMath(t, false)));
    s = s.replace(new RegExp(`${PH_OPEN}C(\\d+)${PH_CLOSE}`, "g"), (_, i) => codes[+i]);
    return { text: s, maths };
  }

  let configured = false;
  function configure() {
    if (configured || !window.marked || !window.DOMPurify) return;
    window.marked.setOptions({ gfm: true, breaks: true });
    window.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer nofollow");
      }
    });
    configured = true;
  }

  function decorateCode(el, highlight) {
    el.querySelectorAll("pre > code").forEach((code) => {
      const pre = code.parentElement;
      if (pre.parentElement && pre.parentElement.classList.contains("code-block")) return;
      const lang = ((code.className.match(/language-([\w+#-]+)/) || [])[1] || "").toLowerCase();
      const wrap = document.createElement("div");
      wrap.className = "code-block";
      const head = document.createElement("div");
      head.className = "code-head";
      const label = document.createElement("span");
      label.textContent = lang || "text";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.innerHTML = '<i class="fa-regular fa-copy me-1"></i>复制';
      btn.addEventListener("click", () => {
        copyText(code.innerText).then(() => {
          btn.innerHTML = '<i class="fa-solid fa-check me-1"></i>已复制';
          setTimeout(() => (btn.innerHTML = '<i class="fa-regular fa-copy me-1"></i>复制'), 1400);
        });
      });
      head.append(label, btn);
      pre.replaceWith(wrap);
      wrap.append(head, pre);
    });
    if (highlight && window.hljs) {
      el.querySelectorAll("pre > code:not(.hljs)").forEach((code) => {
        try {
          window.hljs.highlightElement(code);
        } catch (e) {
          /* 未知语言忽略 */
        }
      });
    }
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } finally {
      ta.remove();
    }
    return Promise.resolve();
  }

  function render(el, text, opts) {
    const final = !opts || opts.final !== false;
    configure();
    if (!window.marked || !window.DOMPurify) {
      el.textContent = text; // 依赖库未加载时降级为纯文本
      el.style.whiteSpace = "pre-wrap";
      return;
    }
    const { text: src, maths } = protectMath(text || "");
    let html = window.DOMPurify.sanitize(window.marked.parse(src), { ADD_ATTR: ["target"] });
    html = html.replace(new RegExp(`${PH_OPEN}M(\\d+)${PH_CLOSE}`, "g"), (_, i) => maths[+i] || "");
    el.innerHTML = html;
    decorateCode(el, final);
  }

  window.MD = { render, copyText, escapeHtml };
})();
