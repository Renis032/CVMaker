(() => {
  const form = document.getElementById("search-form");
  if (!form) return;
  const out = document.getElementById("search-summary");
  const remoteBox = form.querySelector(".when-remote");
  const any = document.getElementById("sen-any");
  const levels = [...form.querySelectorAll("input[name=seniority]")];
  const list = name => (form.elements[name]?.value || "").split(",").map(s => s.trim()).filter(Boolean);
  const words = (xs, joiner = "or") => xs.length <= 1 ? (xs[0] || "") : xs.slice(0, -1).join(", ") + " " + joiner + " " + xs[xs.length - 1];
  const q = xs => xs.map(x => "“" + x + "”");

  any.addEventListener("change", () => { if (any.checked) levels.forEach(l => l.checked = false); else if (!levels.some(l => l.checked)) any.checked = true; });
  levels.forEach(l => l.addEventListener("change", () => { any.checked = !levels.some(x => x.checked); }));
  document.getElementById("sources-clear").addEventListener("click", e => {
    e.preventDefault(); form.querySelectorAll("input[name=sources]").forEach(c => c.checked = false); update();
  });

  function update() {
    const where = form.querySelector("input[name=where]:checked")?.value || "both";
    remoteBox.hidden = where === "local";
    const titles = list("role_keywords"), skills = list("skill_keywords"), countries = list("countries");
    const exclude = list("exclude"), must = list("must_mention");
    const sen = levels.filter(l => l.checked).map(l => l.value);
    const age = form.elements.max_age_days.value;
    let place;
    if (where === "remote") place = "fully remote";
    else if (!countries.length) place = where === "both" ? "remote (no countries set)" : "nowhere yet - add a country";
    else place = "in " + words(countries) + (where === "both" ? ", plus fully remote" : "");
    const cats = [...form.querySelectorAll("input[name=categories]:checked")].map(c => c.dataset.name);
    const what = cats.length ? words(cats) + (titles.length ? " (or titles with " + words(q(titles)) + ")" : "") + " jobs " :
      titles.length ? words(q(titles)) + " jobs " : "jobs with any title ";
    let s = "Finds " + (sen.length ? words(sen) + " " : "") + what + place;
    s += ", posted in the last " + age + " days";
    if (form.elements.salary_only.checked) s += ", with a salary";
    if (exclude.length) s += ", skipping " + words(q(exclude));
    if (must.length) s += ", whose description mentions " + words(q(must));
    s += ".";
    if (skills.length) s += " Ranked by " + words(skills, "and") + ".";
    const picked = form.querySelectorAll("input[name=sources]:checked").length;
    document.getElementById("sources-state").textContent = picked ? picked + " picked" : "every source that covers your countries";
    s += picked ? " Only " + picked + " picked source" + (picked > 1 ? "s" : "") + "." : "";
    out.textContent = s;
  }
  ["input", "change", "click", "focusout", "keyup"].forEach(ev => form.addEventListener(ev, () => setTimeout(update, 0)));
  update();
})();
