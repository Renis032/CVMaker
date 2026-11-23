document.querySelectorAll(".practice textarea[name=answer]").forEach(t => {
  const out = t.closest("form").querySelector(".answer-count");
  if (!out) return;
  const show = () => { const n = t.value.trim().split(/\s+/).filter(Boolean).length; out.textContent = n ? `${n} words · about ${Math.max(1, Math.round(n / 140))} min spoken` : ""; };
  t.addEventListener("input", show);
});
