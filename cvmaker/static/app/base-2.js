// Page tabs: <nav class="page-tabs" data-alias='{"old-anchor": "tab"}'> with <a data-tab> links and
// <section class="tab-panel" data-tab> panels. The URL hash picks the tab, so links and redirects can target one.
(() => {
  const bar = document.querySelector(".page-tabs");
  if (!bar) return;
  const tabs = [...bar.querySelectorAll("a[data-tab]")];
  const panels = [...document.querySelectorAll(".tab-panel")];
  const alias = JSON.parse(bar.dataset.alias || "{}");
  const names = new Set(tabs.map(t => t.dataset.tab));
  const header = document.querySelector("header.topbar");
  const headH = () => header ? header.offsetHeight : 0;
  document.documentElement.style.setProperty("--head-h", headH() + "px");

  function show(name, scroll) {
    name = alias[name] || name;
    if (!names.has(name)) name = tabs[0].dataset.tab;
    tabs.forEach(t => t.classList.toggle("on", t.dataset.tab === name));
    panels.forEach(p => p.classList.toggle("on", p.dataset.tab === name));
    if (scroll && bar.getBoundingClientRect().top < headH() + 1) window.scrollTo({ top: bar.offsetTop - headH() });
  }
  document.body.classList.add("tabs-on");
  show(location.hash.slice(1), false);
  window.addEventListener("hashchange", () => show(location.hash.slice(1), true));
})();
