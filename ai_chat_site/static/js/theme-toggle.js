(function () {
  var btn = document.getElementById("btnTheme");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    document.documentElement.setAttribute("data-bs-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
    document.dispatchEvent(new CustomEvent("themechange", { detail: next }));
  });
})();
