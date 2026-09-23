// 首屏前确定主题，避免闪烁（CSP 禁止内联脚本，所以单独成文件）
(function () {
  var t = null;
  try { t = localStorage.getItem("theme"); } catch (e) {}
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", t);
  document.documentElement.setAttribute("data-bs-theme", t);
})();
