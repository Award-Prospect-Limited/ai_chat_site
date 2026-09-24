(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const fmt = (n) => {
    n = Number(n || 0);
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e4) return (n / 1e3).toFixed(1) + "k";
    return n.toLocaleString();
  };
  const day = (s) => (s ? String(s).slice(0, 10) : "—");
  const ago = (s) => {
    if (!s) return "—";
    const d = new Date(String(s).replace(" ", "T") + "Z");
    const m = (Date.now() - d) / 60000;
    if (m < 60) return `${Math.max(1, Math.round(m))} 分钟前`;
    if (m < 1440) return `${Math.round(m / 60)} 小时前`;
    if (m < 43200) return `${Math.round(m / 1440)} 天前`;
    return day(s);
  };

  async function api(url, method, body) {
    const res = await fetch(url, {
      method: method || "GET",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
    return data;
  }

  function render(d) {
    const users = d.users || [];
    const sum = (k) => users.reduce((a, u) => a + Number(u[k] || 0), 0);
    const active7 = users.filter((u) => u.last_message_at && Date.now() - new Date(u.last_message_at.replace(" ", "T") + "Z") < 7 * 864e5).length;
    const unused = (d.invites || []).filter((i) => !i.used_at && !i.disabled).length;
    $("cards").innerHTML = [
      ["用户", users.length],
      ["七天活跃", active7],
      ["本周 Token", fmt(sum("week_tokens"))],
      ["本月 Token", fmt(sum("month_tokens"))],
      ["累计 Token", fmt(sum("total_tokens"))],
      ["可用邀请码", unused],
    ]
      .map(([k, v]) => `<div class="admin-card"><div class="k">${k}</div><div class="v">${v}</div></div>`)
      .join("");

    // 近 30 天柱状图（补齐没有数据的日期）
    const map = Object.fromEntries((d.daily || []).map((r) => [r.day, r]));
    const days = [];
    for (let i = 29; i >= 0; i--) {
      const t = new Date(Date.now() - i * 864e5);
      days.push(t.toISOString().slice(0, 10));
    }
    const vals = days.map((k) => (map[k] ? map[k].replies : 0));
    const max = Math.max(1, ...vals);
    $("bars").innerHTML = days
      .map((k, i) => `<div class="bar" style="height:${(vals[i] / max) * 100}%" title="${k}：${vals[i]} 次回复，${fmt(map[k] ? map[k].tokens : 0)} tokens"></div>`)
      .join("");
    $("barsX").innerHTML = `<span>${days[0].slice(5)}</span><span>${days[14].slice(5)}</span><span>${days[29].slice(5)}</span>`;
    $("dailyTotal").textContent = `共 ${vals.reduce((a, b) => a + b, 0)} 次`;

    const bm = d.by_model || [];
    const maxT = Math.max(1, ...bm.map((r) => r.tokens));
    $("byModel").innerHTML = bm.length
      ? bm
          .map(
            (r) =>
              `<div class="bar-row"><span class="name" title="${esc(r.model_name)}">${esc(r.model_name || "未知")}</span><span class="track"><span class="fill" style="width:${
                (r.tokens / maxT) * 100
              }%; display:block"></span></span><span class="val">${fmt(r.tokens)}</span></div>`
          )
          .join("")
      : `<div style="color: var(--muted); font-size: .85rem">本月还没有数据</div>`;

    $("users").innerHTML = users
      .map(
        (u) => `<tr>
          <td><b>${esc(u.username)}</b></td>
          <td class="mono">${esc(u.email)}</td>
          <td class="mono">${day(u.created_at)}</td>
          <td>${ago(u.last_message_at)}</td>
          <td class="num">${u.conversations}</td>
          <td class="num">${u.messages}</td>
          <td class="num">${fmt(u.week_tokens)}</td>
          <td class="num">${fmt(u.month_tokens)}</td>
          <td class="num">${fmt(u.total_tokens)}</td>
          <td>${u.disabled ? '<span class="pill off">已停用</span>' : '<span class="pill ok">正常</span>'}</td>
          <td>${
            u.username === d.me
              ? ""
              : `<button class="link-btn ${u.disabled ? "" : "danger"}" data-user="${u.id}" data-disabled="${u.disabled ? 0 : 1}">${u.disabled ? "恢复" : "停用"}</button>`
          }</td>
        </tr>`
      )
      .join("");

    $("invites").innerHTML =
      (d.invites || [])
        .map((i) => {
          const status = i.used_at ? '<span class="pill used">已使用</span>' : i.disabled ? '<span class="pill off">已作废</span>' : '<span class="pill ok">可用</span>';
          const act = i.used_at
            ? ""
            : `<button class="link-btn" data-copy="${esc(i.code)}">复制</button> · <button class="link-btn ${i.disabled ? "" : "danger"}" data-invite="${i.id}" data-disabled="${
                i.disabled ? 0 : 1
              }">${i.disabled ? "恢复" : "作废"}</button>`;
          return `<tr><td class="mono">${esc(i.code)}</td><td class="mono">${day(i.created_at)}</td><td>${status}</td><td>${esc(i.used_by || "—")}</td><td>${act}</td></tr>`;
        })
        .join("") || `<tr><td colspan="5" style="color: var(--muted)">还没有邀请码</td></tr>`;
  }

  async function load() {
    render(await api("/api/admin/overview"));
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.id === "btnInvite") {
        const r = await api("/api/admin/invites", "POST", { count: Number($("inviteCount").value) });
        await load();
        // 异步请求之后部分浏览器（如 Safari）不允许写剪贴板；失败不影响生成结果
        let copied = false;
        try {
          await navigator.clipboard.writeText(r.codes.join("\n"));
          copied = true;
        } catch (e) {}
        b.innerHTML = `<i class="fa-solid fa-check me-1"></i>${copied ? "已生成并复制" : `已生成 ${r.codes.length} 个`}`;
        setTimeout(() => (b.innerHTML = '<i class="fa-solid fa-plus me-1"></i>生成'), 2000);
      } else if (b.dataset.copy) {
        try {
          await navigator.clipboard.writeText(b.dataset.copy);
          b.textContent = "已复制";
        } catch (err) {
          window.prompt("复制失败，请手动复制：", b.dataset.copy);
        }
      } else if (b.dataset.invite) {
        await api(`/api/admin/invites/${b.dataset.invite}`, "PATCH", { disabled: b.dataset.disabled === "1" });
        await load();
      } else if (b.dataset.user) {
        if (b.dataset.disabled === "1" && !confirm("停用后该用户将无法登录，确定吗？")) return;
        await api(`/api/admin/users/${b.dataset.user}`, "PATCH", { disabled: b.dataset.disabled === "1" });
        await load();
      }
    } catch (err) {
      alert(err.message);
    }
  });

  load().catch((e) => alert(e.message));
})();
