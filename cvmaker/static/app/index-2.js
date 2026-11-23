(() => {
  const view = document.getElementById("jobs-view");
  const qBox = document.getElementById("job-q");
  const header = document.querySelector("header.topbar");
  if (header) document.documentElement.style.setProperty("--head-h", header.offsetHeight + "px");

  // Density is a class on the view itself, so it survives the list being replaced.
  let density = "comfortable";
  try { density = localStorage.getItem("jobs-density") || density; } catch (e) {}
  function applyDensity() {
    view.classList.toggle("compact", density === "compact");
    view.querySelectorAll("[data-density]").forEach(b => b.classList.toggle("on", b.dataset.density === density));
  }

  // Filters, tabs, sort and "show more" replace the list in place instead of reloading the page.
  let request = null;
  const openGroups = () => Object.fromEntries([...view.querySelectorAll("details[data-key]")].map(d => [d.dataset.key, d.open]));
  function render(html, url, { push = true, keepScroll = true } = {}) {
    const next = new DOMParser().parseFromString(html, "text/html").getElementById("jobs-view");
    if (!next) { location.href = url; return; }
    const active = document.activeElement, groups = openGroups(), filterScroll = view.querySelector(".filters")?.scrollTop || 0;
    const focusKey = view.contains(active) && active.name ? [active.name, active.value, active.type] : null;
    view.innerHTML = next.innerHTML;
    view.querySelectorAll("details[data-key]").forEach(d => { if (groups[d.dataset.key]) d.open = true; });
    window.enhanceSelects?.(view);
    applyDensity();
    const filters = view.querySelector(".filters");
    if (filters) filters.scrollTop = filterScroll;
    if (focusKey) {
      const again = [...view.querySelectorAll(`[name="${CSS.escape(focusKey[0])}"]`)].find(el => el.type !== "checkbox" && el.type !== "radio" || el.value === focusKey[1]);
      (again?.closest(".cs")?.querySelector(".cs-btn") || again)?.focus({ preventScroll: true });
    }
    if (push) history.pushState({ jobs: true }, "", url);
    if (!keepScroll) {
      const top = view.getBoundingClientRect().top + scrollY - (header?.offsetHeight || 0) - 12;
      if (scrollY > top) scrollTo({ top });
    }
  }
  async function go(url, options = {}) {
    request?.abort();
    request = new AbortController();
    view.classList.add("loading");
    try {
      const res = await fetch(url, { signal: request.signal, method: options.method || "GET", body: options.body });
      if (!res.ok) throw new Error(res.status);
      render(await res.text(), options.method ? res.url : url, options);
    } catch (e) {
      if (e.name === "AbortError") return;
      if (options.method) location.reload(); else location.href = url;
    } finally { view.classList.remove("loading"); }
  }

  // The filter form's address: only what is set, and no sort when it is the natural one.
  function formUrl() {
    const form = document.getElementById("filter-form");
    const params = new URLSearchParams();
    for (const [k, v] of new FormData(form)) if (String(v).trim() && !(k === "score" && v === "0")) params.append(k, String(v).trim());
    const natural = params.get("q") ? "relevance" : "match";
    const before = new URLSearchParams(location.search);
    // A new or removed word goes back to the natural order; picking a sort by hand keeps it.
    if (params.get("sort") === natural || (params.get("q") || "") !== (before.get("q") || "") && !before.get("sort")) params.delete("sort");
    const s = params.toString();
    return s ? "/?" + s : "/";
  }
  const submit = (options) => go(formUrl(), { keepScroll: false, ...options });

  let typing = 0;
  document.addEventListener("change", e => {
    if (e.target.form?.id !== "filter-form" || e.target === qBox) return;
    submit();
  });
  qBox.addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(() => submit({ keepScroll: true }), 250); });
  qBox.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(typing); submit(); }
    else if (e.key === "Escape" && qBox.value) { e.preventDefault(); qBox.value = ""; clearTimeout(typing); submit(); }
  });
  document.addEventListener("submit", e => {
    if (e.target.id === "filter-form") { e.preventDefault(); submit(); }
    else if (e.target.matches("#jobs-view form[data-inline]")) {
      // Like, unlike, hide: send it and redraw the list where you are.
      e.preventDefault();
      e.target.closest("details")?.removeAttribute("open");
      e.target.closest(".jcard")?.classList.add("leaving");
      go(e.target.action, { method: "POST", body: new URLSearchParams(new FormData(e.target, e.submitter)), push: false });
    }
  });
  view.addEventListener("click", e => {
    const density_ = e.target.closest("[data-density]");
    if (density_) {
      density = density_.dataset.density;
      try { localStorage.setItem("jobs-density", density); } catch (err) {}
      return applyDensity();
    }
    if (e.target.closest("[data-filters-toggle]")) return view.classList.toggle("filters-open");
    if (view.classList.contains("filters-open") && !e.target.closest(".filters")) return view.classList.remove("filters-open");
    const link = e.target.closest("a[data-nav]");
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    const url = new URL(link.href);
    if (!url.searchParams.has("q")) qBox.value = "";
    go(url.pathname + url.search, { keepScroll: link.hasAttribute("data-keep-scroll") });
  });
  addEventListener("popstate", () => {
    qBox.value = new URLSearchParams(location.search).get("q") || "";
    go(location.pathname + location.search, { push: false });
  });

  // "/" jumps to the search box; the tips close on a click elsewhere.
  document.addEventListener("keydown", e => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
    e.preventDefault(); qBox.focus(); qBox.select();
  });
  const tips = document.querySelector(".qhelp");
  document.addEventListener("click", e => { if (tips.open && !tips.contains(e.target)) tips.open = false; });

  applyDensity();

  const dlg = document.getElementById("search-settings");
  document.addEventListener("click", e => { if (e.target.closest("[data-open-settings]")) dlg.showModal(); });
  dlg.querySelector("[data-close-settings]").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); });
  if (dlg.hasAttribute("data-open")) dlg.showModal();
})();
