(() => {
  const box = document.getElementById("add-text"), hint = document.getElementById("add-kind");
  const base = hint.textContent;
  box.addEventListener("input", () => {
    const v = box.value.trim();
    if (/^https?:\/\/\S+$/.test(v)) hint.textContent = /linkedin\.com|jobs\.bg/.test(v) ? "⚠ The app doesn't load this site. Paste the posting text instead." : "A link: the app loads the page, then Claude reads it.";
    else hint.textContent = v ? `Pasted text: ${v.split(/\s+/).length} words. Claude reads it as it is.` : base;
  });
})();
