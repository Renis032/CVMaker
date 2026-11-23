document.querySelectorAll("form[data-slow]").forEach(f => f.addEventListener("submit", () => {
  f.classList.add("working");
  f.querySelectorAll("button").forEach(b => b.disabled = true);
}));
// Top-bar menus (person, bell): one open at a time, closed by a click elsewhere or Esc.
const topMenus = [...document.querySelectorAll(".person-menu, .bell-menu")];
topMenus.forEach(m => m.addEventListener("toggle", () => { if (m.open) topMenus.forEach(o => { if (o !== m) o.open = false; }); }));
document.addEventListener("click", e => topMenus.forEach(m => { if (m.open && !m.contains(e.target)) m.open = false; }));
document.addEventListener("keydown", e => { if (e.key === "Escape") topMenus.forEach(m => { if (m.open) { m.open = false; m.querySelector("summary").focus(); } }); });
// The bell's count and list follow new notifications without a reload.
const bellCount = document.querySelector(".bell-n");
let bellUnread = Number(bellCount?.textContent || 0);
const agoText = ts => { const s = Date.now() / 1000 - ts; return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`; };
async function refreshBell(unread) {
  if (!bellCount || unread === bellUnread) return;
  bellUnread = unread;
  bellCount.textContent = unread; bellCount.hidden = !unread;
  document.querySelector(".bell-head .link-btn").disabled = !unread;
  try {
    const data = await (await fetch("/api/notifications", { cache: "no-store" })).json();
    const list = document.querySelector(".bell-list");
    if (!data.items.length) return;
    list.replaceChildren(...data.items.map(n => {
      const a = Object.assign(document.createElement("a"), { className: `bell-item k-${n.kind}${n.read ? "" : " unread"}`, href: `/notifications/${n.id}/open` });
      const text = Object.assign(document.createElement("span"), { className: "bi-text" });
      text.append(Object.assign(document.createElement("span"), { className: "bi-title", textContent: n.title }));
      if (n.body) text.append(Object.assign(document.createElement("span"), { className: "bi-body", textContent: n.body }));
      text.append(Object.assign(document.createElement("span"), { className: "bi-time", textContent: agoText(n.created) }));
      a.append(Object.assign(document.createElement("span"), { className: "bi-ico" }), text);
      return a;
    }));
  } catch (e) {}
}
// Quick search (top bar): the best matching jobs and companies while typing; Enter shows the full list. "/" focuses it.
(() => {
  const form = document.querySelector(".quick-search");
  if (!form || getComputedStyle(form).visibility === "hidden") return;
  const input = form.querySelector("input"), panel = form.querySelector(".qs-panel");
  let items = [], active = -1, timer = 0, asked = "";
  const el = (tag, props, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };
  const close = () => { panel.hidden = true; active = -1; input.setAttribute("aria-expanded", "false"); };
  function setActive(i) {
    active = i;
    items.forEach((a, ix) => a.classList.toggle("active", ix === i));
  }
  function show(data, q) {
    panel.replaceChildren();
    if (data.jobs.length) panel.append(el("div", { className: "qs-h", textContent: "Jobs" }));
    for (const j of data.jobs) {
      const a = el("a", { className: "qs-item", href: "/job/" + j.id },
        el("span", { className: "qs-t" }, el("b", { textContent: j.title }), el("small", { textContent: [j.company, j.location].filter(Boolean).join(" · ") })));
      if (j.fit !== null) a.append(el("span", { className: "fitb " + (j.fit >= 7 ? "f-hi" : j.fit >= 5 ? "f-mid" : "f-lo"), textContent: j.fit }));
      panel.append(a);
    }
    if (data.companies.length) panel.append(el("div", { className: "qs-h", textContent: "Companies" }));
    for (const c of data.companies) panel.append(el("a", { className: "qs-item", href: "/?company=" + encodeURIComponent(c.name) },
      el("span", { className: "qs-t" }, el("b", { textContent: c.name }), el("small", { textContent: c.count + (c.count === 1 ? " job" : " jobs") }))));
    if (data.total) panel.append(el("a", { className: "qs-item all", href: "/?q=" + encodeURIComponent(q), textContent: `See all ${data.total} matching jobs →` }));
    else if (!data.companies.length) panel.append(el("div", { className: "qs-none", textContent: "No saved jobs match. Press Enter to search anyway." }));
    items = [...panel.querySelectorAll("a")];
    active = -1;
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  async function ask() {
    const q = input.value.trim();
    if (q.length < 2) return close();
    asked = q;
    try {
      const data = await (await fetch("/api/suggest?q=" + encodeURIComponent(q))).json();
      if (asked === q && document.activeElement === input) show(data, q);
    } catch (e) {}
  }
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(ask, 180); });
  input.addEventListener("focus", () => { if (input.value.trim().length >= 2) ask(); });
  input.addEventListener("keydown", e => {
    if (e.key === "Escape") { if (!panel.hidden) { e.preventDefault(); e.stopPropagation(); close(); } else input.blur(); }
    else if (panel.hidden || !items.length) return;
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((active + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((active - 1 + items.length) % items.length); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); location.href = items[active].href; }
  });
  document.addEventListener("click", e => { if (!form.contains(e.target)) close(); });
  document.addEventListener("keydown", e => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
    if (document.body.classList.contains("app-page")) return;  // the CV editor has its own keys
    e.preventDefault(); input.focus(); input.select();
  });
})();

document.querySelectorAll("[data-nav-toggle], [data-nav-close]").forEach(b => b.addEventListener("click", () => document.body.classList.toggle("nav-open")));
document.querySelector(".theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
});

// Chip inputs: <input data-chips data-list="datalist-id" [data-upper]> keeps a comma-separated value.
let dirty = false;
document.addEventListener("input", e => { if (e.target.closest("form")) dirty = true; });
document.querySelectorAll("input[data-chips]").forEach(src => {
  const box = document.createElement("div");
  box.className = "chips";
  const entry = document.createElement("input");
  entry.type = "text";
  entry.placeholder = src.placeholder || "type, then Enter";
  entry.setAttribute("aria-label", src.closest("div")?.querySelector("label")?.textContent || src.name);
  if (src.dataset.list) entry.setAttribute("list", src.dataset.list);
  const upper = "upper" in src.dataset;
  let items = src.value.split(",").map(s => s.trim()).filter(Boolean);
  const render = () => {
    box.querySelectorAll(".chip").forEach(c => c.remove());
    items.forEach((item, i) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = item;
      const x = document.createElement("button");
      x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Remove " + item);
      x.addEventListener("click", ev => { ev.stopPropagation(); items.splice(i, 1); dirty = true; render(); });
      chip.appendChild(x);
      box.insertBefore(chip, entry);
    });
    src.value = items.join(", ");
  };
  const add = value => {
    value.split(",").map(v => upper ? v.trim().toUpperCase() : v.trim()).filter(Boolean).forEach(v => {
      if (!items.some(x => x.toLowerCase() === v.toLowerCase())) items.push(v);
    });
    entry.value = "";
    render();
  };
  entry.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(entry.value); }
    else if (e.key === "Backspace" && !entry.value && items.length) { items.pop(); render(); }
  });
  entry.addEventListener("input", e => { if (!e.inputType || e.inputType === "insertReplacementText") add(entry.value); });
  entry.addEventListener("blur", () => add(entry.value));
  box.addEventListener("click", () => entry.focus());
  src.type = "hidden";
  src.after(box);
  box.appendChild(entry);
  render();
});

// Background tasks: show what's running, update progress in place, and announce results.
const indicator = document.getElementById("task-indicator");
const toasts = Object.assign(document.createElement("div"), { id: "toasts" });
document.body.appendChild(toasts);
const elapsed = s => { s = Math.max(0, Math.round(s)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`; };
let skew = 0;
setInterval(() => document.querySelectorAll(".elapsed[data-since]").forEach(el => {
  el.textContent = elapsed(Date.now() / 1000 - skew - parseFloat(el.dataset.since));
}), 1000);

function renderSources(el, sources) {
  el.replaceChildren(...Object.entries(sources).map(([name, v]) => {
    const p = document.createElement("span");
    p.className = "pill " + (v === null ? "" : v === "error" ? "err" : "ok");
    p.textContent = v === null ? `${name} …` : v === "error" ? `${name} ✗` : `${name} ✓ ${v}`;
    return p;
  }));
}

function toast(t, linkText) {
  const box = document.createElement("div");
  box.className = "toast" + (t.status === "error" ? " error" : "");
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = t.status === "error" ? `Failed: ${t.label}` : `Done: ${t.label}`;
  body.appendChild(title);
  if (t.status === "error") {
    const err = document.createElement("div");
    err.className = "muted"; err.textContent = t.error;
    body.appendChild(err);
  } else if (t.url) {
    const a = document.createElement("a");
    a.href = t.url; a.textContent = linkText || "Open →";
    body.append(document.createElement("br"), a);
  }
  const x = Object.assign(document.createElement("button"), { className: "x", type: "button", textContent: "×" });
  x.setAttribute("aria-label", "Dismiss");
  x.addEventListener("click", () => box.remove());
  box.append(body, x);
  toasts.appendChild(box);
  if (t.status !== "error") setTimeout(() => box.remove(), 15000);
}

function goTo(url) {
  const target = new URL(url, location.href);
  if (target.pathname + target.search === location.pathname + location.search) {
    if (target.hash) location.hash = target.hash;  // setting an empty hash would scroll the page to the top
    location.reload();
  } else location.href = target.href;
}

async function poll() {
  let data;
  try { data = await (await fetch("/api/tasks", { cache: "no-store" })).json(); }
  catch (e) { return setTimeout(poll, 10000); }
  skew = Date.now() / 1000 - data.now;
  if (typeof data.unread === "number") refreshBell(data.unread);
  indicator.hidden = !data.active.length;
  indicator.querySelector(".n").textContent = `${data.active.length} running`;
  indicator.title = data.active.map(t => t.label).join("\n");
  for (const t of data.active) {
    document.querySelectorAll(`[data-task-id="${t.id}"]`).forEach(el => {
      const step = el.querySelector(".task-step");
      if (step && t.progress.total) step.textContent = `${t.progress.done} of ${t.progress.total} rated`;
      else if (step && t.progress.step) step.textContent = t.progress.step;
      else if (step && t.status === "running" && step.textContent.startsWith("waiting")) step.textContent = "working";
      const since = el.querySelector(".elapsed");
      if (since && t.started) since.dataset.since = t.started;
      const sources = el.querySelector(".src-progress");
      if (sources && t.progress.sources) renderSources(sources, t.progress.sources);
    });
  }
  for (const t of data.finished) {
    fetch(`/api/tasks/${t.id}/seen`, { method: "POST" });
    const here = document.querySelector(`[data-task-id="${t.id}"]`);
    const samePath = t.url && new URL(t.url, location.href).pathname === location.pathname;
    if (here && samePath && !dirty && t.status === "done") return goTo(t.url);
    if (here && here.closest("[data-follow]") && !dirty && t.status === "done" && t.url) return goTo(t.url);
    if (here) {
      here.classList.add("done");
      here.replaceChildren(Object.assign(document.createElement("span"), {
        textContent: t.status === "done" ? "✓ Done." : `✗ Failed: ${t.error}`,
      }));
      if (t.status === "done" && t.url) here.appendChild(Object.assign(document.createElement("a"), { href: t.url, textContent: " Open the result →" }));
    }
    toast(t);
  }
  setTimeout(poll, data.active.length ? 2000 : 15000);
}
poll();

// Hide menus open as a fixed overlay, so a scrolling table can't clip them.
document.addEventListener("toggle", e => {
  const d = e.target;
  if (!d.matches?.("details.hide-menu") || !d.open) return;
  const menu = d.querySelector(".menu"), r = d.querySelector("summary").getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.right = "auto";
  menu.style.left = Math.max(8, Math.min(r.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8)) + "px";
  const below = r.bottom + 4, fits = below + menu.offsetHeight < innerHeight;
  menu.style.top = (fits ? below : Math.max(8, r.top - menu.offsetHeight - 4)) + "px";
}, true);  // "toggle" does not bubble, so listen on the way down
addEventListener("scroll", () => document.querySelectorAll("details.hide-menu[open]").forEach(d => d.open = false), { passive: true });
// Close an open hide menu when clicking elsewhere.
document.addEventListener("click", e => {
  document.querySelectorAll("details.hide-menu[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
