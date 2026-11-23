document.getElementById("key-test")?.addEventListener("click", async () => {
  const out = document.getElementById("key-test-result");
  out.textContent = "Testing…";
  try {
    const r = await fetch("/settings/keys/test", { method: "POST" });
    const j = await r.json();
    out.textContent = j.message;
    out.style.color = j.ok ? "var(--good)" : "var(--bad)";
  } catch (e) { out.textContent = "Couldn't test it."; }
});
