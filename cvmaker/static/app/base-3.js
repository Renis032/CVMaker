// Styled dropdowns: wrap each <select> in a button + list in the site's style. The select stays in the form
// (hidden), so submitting and onchange handlers work as before. Add data-native to keep a browser select.
(() => {
  const chev = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>';
  const tick = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  let openBox = null;
  function close() {
    if (!openBox) return;
    openBox.box.classList.remove("open");
    openBox.list.remove();
    openBox.btn.setAttribute("aria-expanded", "false");
    openBox = null;
  }
  function enhance(sel) {
    if (sel.multiple || sel.size > 1 || sel.dataset.native !== undefined || sel.closest(".cs")) return;
    const box = document.createElement("span");
    const autoWidth = getComputedStyle(sel).width !== getComputedStyle(sel.parentElement).width || sel.style.width;
    box.className = "cs" + (autoWidth ? " auto" : "");
    if (sel.style.width) box.style.width = sel.style.width;
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "cs-btn";
    btn.setAttribute("aria-haspopup", "listbox"); btn.setAttribute("aria-expanded", "false");
    if (sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
    if (sel.title) btn.title = sel.title;
    const val = document.createElement("span"); val.className = "cs-val";
    btn.append(val); btn.insertAdjacentHTML("beforeend", chev);
    sel.parentNode.insertBefore(box, sel); box.append(sel, btn);
    sel.tabIndex = -1;
    if (sel.id) document.querySelectorAll(`label[for="${CSS.escape(sel.id)}"]`).forEach(l => l.addEventListener("click", e => { e.preventDefault(); btn.focus(); }));
    const sync = () => { val.textContent = sel.selectedOptions[0]?.textContent.trim() || ""; box.classList.toggle("disabled", sel.disabled); };
    sync();
    sel.addEventListener("change", sync);
    let active = 0;
    const options = () => [...sel.options];
    function open() {
      close();
      if (sel.disabled) return;
      const list = document.createElement("div");
      list.className = "cs-list"; list.setAttribute("role", "listbox");
      let group = null;
      options().forEach((o, i) => {
        if (o.parentElement.tagName === "OPTGROUP" && o.parentElement !== group) {
          group = o.parentElement;
          list.append(Object.assign(document.createElement("div"), { className: "cs-group", textContent: group.label }));
        }
        const b = document.createElement("button");
        b.type = "button"; b.className = "cs-opt" + (o.selected ? " on" : ""); b.dataset.ix = i; b.disabled = o.disabled;
        b.setAttribute("role", "option");
        b.append(Object.assign(document.createElement("span"), { textContent: o.textContent.trim() }));
        if (o.selected) b.insertAdjacentHTML("beforeend", tick);
        b.addEventListener("mousedown", e => e.preventDefault());
        b.addEventListener("click", () => choose(i));
        b.addEventListener("mouseenter", () => setActive(i));
        list.append(b);
      });
      document.body.append(list);
      const r = btn.getBoundingClientRect();
      list.style.minWidth = r.width + "px";
      list.style.left = Math.min(r.left, innerWidth - list.offsetWidth - 8) + "px";
      const below = innerHeight - r.bottom - 8;
      if (below < Math.min(list.offsetHeight, 240) && r.top > below) list.style.top = Math.max(8, r.top - list.offsetHeight - 6) + "px";
      else { list.style.top = r.bottom + 6 + "px"; list.style.maxHeight = Math.max(160, below - 6) + "px"; }
      // Inside a modal <dialog>, the list must live in the dialog to stay clickable.
      const dlg = sel.closest("dialog[open]");
      if (dlg) dlg.append(list);
      box.classList.add("open"); btn.setAttribute("aria-expanded", "true");
      openBox = { box, btn, list, sel };
      setActive(Math.max(0, sel.selectedIndex));
    }
    function setActive(i) {
      active = i;
      openBox?.list.querySelectorAll(".cs-opt").forEach(b => b.classList.toggle("active", Number(b.dataset.ix) === i));
      // Keep the highlighted option visible by scrolling the list only (scrolling the page would close it).
      const list = openBox?.list, el = list?.querySelector(`.cs-opt[data-ix="${i}"]`);
      if (el) {
        if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop - 5;
        else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight + 5;
      }
    }
    function choose(i) {
      const o = options()[i];
      close();
      btn.focus();
      if (!o || o.disabled || sel.selectedIndex === i) return;
      sel.selectedIndex = i;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    btn.addEventListener("click", () => (openBox?.sel === sel ? close() : open()));
    btn.addEventListener("keydown", e => {
      const isOpen = openBox?.sel === sel;
      const step = d => { let i = active; do { i += d; } while (options()[i]?.disabled); if (options()[i]) setActive(i); };
      if (!isOpen && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
      else if (isOpen && e.key === "ArrowDown") { e.preventDefault(); step(1); }
      else if (isOpen && e.key === "ArrowUp") { e.preventDefault(); step(-1); }
      else if (isOpen && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choose(active); }
      else if (e.key === "Escape" && isOpen) { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "Tab") close();
      else if (isOpen && e.key.length === 1) {
        const i = options().findIndex(o => o.textContent.trim().toLowerCase().startsWith(e.key.toLowerCase()));
        if (i >= 0) setActive(i);
      }
    });
  }
  document.addEventListener("pointerdown", e => { if (openBox && !openBox.box.contains(e.target) && !openBox.list.contains(e.target)) close(); });
  addEventListener("scroll", e => { if (openBox && !openBox.list.contains(e.target)) close(); }, true);
  addEventListener("resize", close);
  document.querySelectorAll("select").forEach(enhance);
  window.enhanceSelects = root => (root || document).querySelectorAll("select").forEach(enhance);
})();
