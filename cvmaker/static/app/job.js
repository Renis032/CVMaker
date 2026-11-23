document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
  const el = document.getElementById(b.dataset.copy);
  try { await navigator.clipboard.writeText(el.value); } catch (e) { el.select(); document.execCommand("copy"); }
  const t = b.textContent; b.textContent = "Copied ✓"; setTimeout(() => b.textContent = t, 1500);
}));
document.querySelectorAll(".chars[data-for]").forEach(out => {
  const el = document.getElementById(out.dataset.for), max = +out.dataset.max;
  const show = () => { out.textContent = `${el.value.length} / ${max} characters`; out.classList.toggle("over", el.value.length > max); };
  el.addEventListener("input", show); show();
});
