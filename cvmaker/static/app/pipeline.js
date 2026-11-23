let dragged = null;
document.querySelectorAll(".jcard").forEach(card => {
  card.addEventListener("dragstart", () => { dragged = card; card.classList.add("dragging"); });
  card.addEventListener("dragend", () => { card.classList.remove("dragging"); dragged = null; });
});
document.querySelectorAll(".col").forEach(col => {
  col.addEventListener("dragover", e => { e.preventDefault(); col.classList.add("over"); });
  col.addEventListener("dragleave", () => col.classList.remove("over"));
  col.addEventListener("drop", async e => {
    e.preventDefault();
    col.classList.remove("over");
    if (!dragged || dragged.closest(".col") === col) return;
    const body = new URLSearchParams({ status: col.dataset.stage });
    const res = await fetch(`/job/${dragged.dataset.job}/status`, { method: "POST", body, headers: { Accept: "application/json" } });
    if (res.ok) location.reload();
  });
});
