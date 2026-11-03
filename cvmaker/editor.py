"""The CV editor (the React app in editor/, built into cvmaker/static/editor): one shared CV in several versions.

Content is not stored here. In "profile" mode the editor shows and edits the profile; in "cv" mode a CV tailored
for a job. What this module keeps (per person, in editor.json) is everything else: the versions (order, what each
one hides, wording a version keeps for itself), the style, section headings, photo and company logos.

Items in the profile have no ids, but versions refer to items (hide this bullet, this project goes first), so ids
are kept in a registry {id: text} and matched back to the items each time: exact text first, then the most similar
text (an item edited elsewhere keeps its id), else a new id.

PDFs: the same React page renders them. The built bundle, the data and the images are written into one HTML file
that headless Chrome prints, so every CV the app makes looks exactly like the editor."""

import base64
import copy
import hashlib
import json
import mimetypes
import re
import secrets
import shutil
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import assistant, settings
from .models import CVContent, Education, Experience, Profile, Project, SkillGroup

STATIC = Path(__file__).parent / "static" / "editor"
CVEDITOR_DATA = Path.home() / "Personal" / "Projects" / "CVEditor" / "data" / "cv.json"

MAIN_SECTIONS = ["summary", "experience", "projects"]
SIDE_SECTIONS = ["contact", "skills", "softSkills", "languages", "interests", "education"]
DEFAULT_HEADINGS = {
    "summary": "Summary", "experience": "Experience", "projects": "Personal projects", "contact": "Contact",
    "skills": "Skills", "softSkills": "Soft skills", "languages": "Languages", "interests": "Interests",
    "education": "Education",
}
DEFAULT_STYLE = {
    "font": "Helvetica, Arial, 'Liberation Sans', sans-serif", "fontSize": 9, "lineHeight": 1.3, "sectionGap": 5,
    "itemGap": 3.5, "sidebarWidth": 35.8, "photoSize": 34, "nameColor": "#1a3a5c", "accent": "#008cff",
    "heading": "#1a3a5c", "text": "#222222", "muted": "#555555", "sidebarBg": "#22405c", "sidebarText": "#ffffff",
    "sidebarLabel": "#a8c5e2",
}
# The page layout, element styles and free elements (text boxes, shapes, icons, images) the editor adds on top.
DEFAULT_PAGE_LAYOUT = {"sidebar": "right", "header": "main", "photo": "side", "headingStyle": "underline",
                       "bullet": "default", "contactIcons": False, "pageBg": "#ffffff"}
# Fonts the editor offers from Google Fonts (editor/src/design.ts FONTS); a PDF loads the ones it uses.
GOOGLE_FONTS = {"Inter", "Roboto", "Open Sans", "Lato", "Source Sans 3", "Montserrat", "Poppins", "Raleway", "Nunito",
                "Work Sans", "DM Sans", "IBM Plex Sans", "Fira Sans", "Rubik", "Josefin Sans", "Oswald", "Merriweather",
                "Playfair Display", "Lora", "PT Serif", "EB Garamond", "Libre Baskerville", "Crimson Text"}
# How similar a changed text must be to still count as the same item (bullet, project...).
SAME_ITEM = 0.6


# --- layout file ---------------------------------------------------------------------------------------------------


def _path() -> Path:
    return settings.DATA_DIR / "editor.json"


def uploads_dir() -> Path:
    return settings.DATA_DIR / "editor_uploads"


def new_variant(profile: CVContent | None, name: str = "Main") -> dict:
    return {
        "id": "v-" + secrets.token_hex(3), "name": name,
        "fileName": re.sub(r"\W+", "_", (profile.name if profile else "") or "CV").strip("_") + "_CV",
        "location": profile.contact.location if profile else "", "overrides": {},
        "mainOrder": list(MAIN_SECTIONS), "sideOrder": list(SIDE_SECTIONS), "projectOrder": [], "hidden": [],
    }


def load_layout() -> dict:
    layout = json.loads(_path().read_text()) if _path().exists() else {}
    profile = assistant.load_profile()
    layout.setdefault("style", dict(DEFAULT_STYLE))
    layout["style"] = {**DEFAULT_STYLE, **layout["style"]}
    layout["headings"] = {**DEFAULT_HEADINGS, **layout.get("headings", {})}
    layout.setdefault("projectsLink", profile.contact.github if profile else "")
    layout.setdefault("photo", "")
    layout.setdefault("logos", {})
    layout.setdefault("contact", ["email", "linkedin", "phone"])
    layout.setdefault("ids", {})
    design = layout.setdefault("design", {})
    design["layout"] = {**DEFAULT_PAGE_LAYOUT, **design.get("layout", {})}
    design.setdefault("elements", {})
    design.setdefault("free", [])
    if not layout.get("variants"):
        layout["variants"] = [new_variant(profile)]
    for v in layout["variants"]:
        v.setdefault("overrides", {})
        # A section can sit in either column; one in neither goes back to its usual column.
        placed = {*v.get("mainOrder", []), *v.get("sideOrder", [])}
        v["mainOrder"] = [*v.get("mainOrder", []), *[k for k in MAIN_SECTIONS if k not in placed]]
        v["sideOrder"] = [*v.get("sideOrder", []), *[k for k in SIDE_SECTIONS if k not in placed]]
    return layout


def save_layout(layout: dict) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(layout, indent=2, ensure_ascii=False))
    tmp.replace(_path())


def exists() -> bool:
    return _path().exists()


# --- ids -------------------------------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:28]


def _match(texts: list[str], reg: dict[str, str], prefix: str, used: set[str], readable: bool = True) -> list[str]:
    """An id for each text: the registry entry with the same text, else the most similar one, else a new id."""
    free = dict(reg)
    ids: list[str | None] = [None] * len(texts)
    for i, t in enumerate(texts):
        hit = next((k for k, v in free.items() if v == t), None)
        if hit:
            ids[i] = hit
            del free[hit]
    for i, t in enumerate(texts):
        if ids[i] or not free:
            continue
        best = max(free, key=lambda k: SequenceMatcher(None, free[k], t).ratio())
        if SequenceMatcher(None, free[best], t).ratio() >= SAME_ITEM:
            ids[i] = best
            del free[best]
    for i, t in enumerate(texts):
        if not ids[i]:
            base = f"{prefix}-{_slug(t)}" if readable and _slug(t) else f"{prefix}-{secrets.token_hex(3)}"
            new, n = base, 2
            while new in used or new in ids:
                new, n = f"{base}-{n}", n + 1
            ids[i] = new
    used.update(ids)
    return ids  # type: ignore[return-value]


def _job_key(e: Experience) -> str:
    return f"{e.company}|{e.title}"


def _dates(e: Experience) -> str:
    return f"{e.start} – {e.end}" if e.start and e.end else (e.start or e.end)


def _split_dates(text: str) -> tuple[str, str]:
    parts = re.split(r"\s+[–—-]\s+|\s*[–—]\s*", text.strip(), maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text.strip(), "")


def _tech_display(tech: str) -> str:
    tech = tech.strip()
    return tech if not tech or tech.startswith("(") else f"({tech})"


def _tech_store(text: str) -> str:
    text = text.strip()
    return text[1:-1].strip() if text.startswith("(") and text.endswith(")") else text


def split_items(value: str) -> list[str]:
    """'C++ (C++14, C++17), Java' -> ['C++ (C++14, C++17)', 'Java']: commas inside brackets don't split."""
    return [x.strip() for x in re.split(r",\s*(?![^()]*\))", value) if x.strip()]


# --- profile / tailored CV -> editor content -----------------------------------------------------------------------


def _contact_value(cv: CVContent, kind: str) -> str:
    if kind.startswith("text:"):
        return kind[5:]
    return getattr(cv.contact, kind, "") if kind in ("email", "phone", "linkedin", "github") else ""


def _contact_kind(line: str) -> str:
    t = line.strip()
    low = t.lower()
    if re.fullmatch(r"\S+@\S+\.\S+", t):
        return "email"
    if "linkedin." in low:
        return "linkedin"
    if "github." in low:
        return "github"
    if re.fullmatch(r"\+?[\d\s()/-]{7,}", t):
        return "phone"
    return "text:" + t


def to_content(cv: CVContent, layout: dict, reg: dict) -> tuple[dict, dict]:
    """The editor's view of a CV (profile or tailored) plus the updated id registry."""
    used: set[str] = set()
    new_reg: dict[str, dict] = {}

    def ids(group: str, texts: list[str], prefix: str, readable: bool = True) -> list[str]:
        out = _match(texts, reg.get(group, {}), prefix, used, readable)
        new_reg[group] = dict(zip(out, texts))
        return out

    jobs = []
    for jid, e in zip(ids("jobs", [_job_key(e) for e in cv.experience], "job"), cv.experience):
        bids = ids(f"b:{jid}", e.bullets, "b", readable=False)
        jobs.append({"id": jid, "role": e.title, "company": e.company, "logo": layout["logos"].get(jid, ""),
                     "dates": _dates(e), "bullets": [{"id": i, "text": t} for i, t in zip(bids, e.bullets)]})
    projects = []
    for pid, p in zip(ids("projects", [p.name for p in cv.projects], "proj"), cv.projects):
        bids = ids(f"b:{pid}", p.bullets, "b", readable=False)
        projects.append({"id": pid, "title": p.name, "tech": _tech_display(p.tech),
                         "bullets": [{"id": i, "text": t} for i, t in zip(bids, p.bullets)]})
    skills = [{"id": sid, "label": g.category, "value": ", ".join(g.items)}
              for sid, g in zip(ids("skills", [g.category for g in cv.skills], "skill"), cv.skills)]
    education = [{"id": eid, "degree": e.degree, "school": e.school}
                 for eid, e in zip(ids("edu", [e.school or e.degree for e in cv.education], "edu"), cv.education)]
    content = {
        "name": cv.name, "title": cv.headline, "photo": layout["photo"] or (photo_url() if settings.PHOTO_PATH.exists() else ""),
        "summary": cv.summary, "contact": [v for v in (_contact_value(cv, k) for k in layout["contact"]) if v],
        "headings": layout["headings"], "projectsLink": layout["projectsLink"], "experience": jobs,
        "projects": projects, "skills": skills, "softSkills": list(cv.soft_skills), "languages": list(cv.languages),
        "interests": list(cv.interests), "education": education,
    }
    return content, new_reg


def use_profile_photo() -> None:
    """A photo uploaded on My profile becomes the CV photo again (the editor may have its own)."""
    if exists():
        layout = load_layout()
        layout["photo"] = ""
        save_layout(layout)


def photo_url() -> str:
    stamp = int(settings.PHOTO_PATH.stat().st_mtime) if settings.PHOTO_PATH.exists() else 0
    return f"/editor/api/photo?v={stamp}"


# --- editor content -> profile / tailored CV ------------------------------------------------------------------------


def from_content(content: dict, base: CVContent, layout: dict, reg: dict) -> tuple[CVContent, dict, list[str]]:
    """Apply the editor's content to a CV. Fields the editor doesn't show (job locations, project links, education
    years, skill details, facts...) are kept from the matching item. Returns (cv, id registry, contact kinds)."""
    old, _ = to_content(base, layout, reg)
    jobs_by_id = {j["id"]: e for j, e in zip(old["experience"], base.experience)}
    projects_by_id = {p["id"]: x for p, x in zip(old["projects"], base.projects)}
    edu_by_id = {e["id"]: x for e, x in zip(old["education"], base.education)}
    cv = base.model_copy(deep=True)
    new_reg: dict[str, dict] = {}

    experience = []
    for j in content["experience"]:
        start, end = _split_dates(j["dates"])
        prev = jobs_by_id.get(j["id"])
        bullets = [b["text"] for b in j["bullets"] if b["text"].strip()]
        experience.append(Experience(title=j["role"], company=j["company"], start=start, end=end,
                                     location=prev.location if prev else "", bullets=bullets))
        new_reg[f"b:{j['id']}"] = {b["id"]: b["text"] for b in j["bullets"] if b["text"].strip()}
    new_reg["jobs"] = {j["id"]: _job_key(e) for j, e in zip(content["experience"], experience)}
    cv.experience = experience

    projects = []
    for p in content["projects"]:
        prev = projects_by_id.get(p["id"])
        projects.append(Project(name=p["title"], tech=_tech_store(p["tech"]), link=prev.link if prev else "",
                                bullets=[b["text"] for b in p["bullets"] if b["text"].strip()]))
        new_reg[f"b:{p['id']}"] = {b["id"]: b["text"] for b in p["bullets"] if b["text"].strip()}
    new_reg["projects"] = {p["id"]: p["title"] for p in content["projects"]}
    cv.projects = projects

    cv.skills = [SkillGroup(category=s["label"], items=split_items(s["value"])) for s in content["skills"]]
    new_reg["skills"] = {s["id"]: s["label"] for s in content["skills"]}
    education = []
    for e in content["education"]:
        prev = edu_by_id.get(e["id"])
        education.append(Education(degree=e["degree"], school=e["school"], years=prev.years if prev else ""))
    new_reg["edu"] = {e["id"]: e["school"] or e["degree"] for e in content["education"]}
    cv.education = education

    cv.name, cv.headline, cv.summary = content["name"], content["title"], content["summary"]
    cv.soft_skills = [x for x in content["softSkills"] if x.strip()]
    cv.languages = [x for x in content.get("languages", []) if x.strip()]
    cv.interests = [x for x in content["interests"] if x.strip()]
    kinds = []
    for line in content["contact"]:
        if not line.strip():
            continue
        kind = _contact_kind(line)
        if not kind.startswith("text:"):
            setattr(cv.contact, kind, line.strip())  # a line removed from the CV keeps its value in the profile
        kinds.append(kind)
    return cv, new_reg, kinds


# --- documents: what the editor loads and saves ----------------------------------------------------------------------


def revision(cv: CVContent) -> str:
    return hashlib.sha1(cv.model_dump_json().encode()).hexdigest()[:16]


def default_cv_variant(layout: dict, label: str) -> dict:
    """The version a tailored CV starts from: your first version's order, sections and location (items differ, so
    hidden items and own wording don't carry over)."""
    first = layout["variants"][0]
    item_ids = re.compile(r"^(job|proj|skill|edu|b)-")
    return {**copy.deepcopy(first), "id": "tailored", "name": label, "overrides": {}, "projectOrder": [],
            "hidden": [h for h in first["hidden"] if not item_ids.match(h)]}


def profile_document() -> dict:
    profile = assistant.load_profile()
    layout = load_layout()
    content, reg = to_content(profile, layout, layout["ids"])
    if reg != layout["ids"]:
        layout["ids"] = reg
        save_layout(layout)
    return {"mode": "profile", "label": "Your CV (profile)", "back": "/profile", "rev": revision(profile),
            "content": content, "variants": layout["variants"], "style": layout["style"], "design": layout["design"],
            "fontPt": None}


def save_profile_document(data: dict) -> str:
    """Save what the editor sent: content into the profile, the rest into editor.json. Returns the new revision."""
    profile = assistant.load_profile()
    if data.get("rev") and data["rev"] != revision(profile):
        raise Conflict("Your profile was changed somewhere else (My profile, the chat…). Reload the editor to get the new version.")
    layout = load_layout()
    cv, reg, kinds = from_content(data["content"], profile, layout, layout["ids"])
    _keep_layout(layout, data, reg, kinds)
    layout["variants"] = data["variants"]
    layout["style"] = data["style"]
    if isinstance(data.get("design"), dict):
        layout["design"] = data["design"]
    assistant.save_profile(cv)  # type: ignore[arg-type]
    save_layout(layout)
    return revision(cv)


def _keep_layout(layout: dict, data: dict, reg: dict, kinds: list[str]) -> None:
    c = data["content"]
    layout["ids"] = reg
    layout["contact"] = kinds
    layout["headings"] = {**DEFAULT_HEADINGS, **c["headings"]}
    layout["projectsLink"] = c["projectsLink"]
    layout["photo"] = "" if c["photo"].startswith("/editor/api/photo") else c["photo"]
    layout["logos"] = {**layout["logos"], **{j["id"]: j["logo"] for j in c["experience"]}}


class Conflict(RuntimeError):
    pass


def cv_document(cv_id: int, label: str, back: str) -> dict:
    from . import db

    _, tailored, _ = db.get_cv(cv_id)
    cv_layout, _ = db.cv_layout(cv_id)
    layout = load_layout()
    content, reg = to_content(tailored.cv, layout, cv_layout.get("editor_ids", {}))
    variant = cv_layout.get("editor_variant") or default_cv_variant(layout, label)
    if reg != cv_layout.get("editor_ids") or "editor_variant" not in cv_layout:
        db.update_cv(cv_id, layout={**cv_layout, "editor_ids": reg, "editor_variant": variant})
    return {"mode": "cv", "label": label, "back": back, "rev": revision(tailored.cv), "content": content,
            "variants": [variant], "style": layout["style"], "design": layout["design"],
            "fontPt": cv_layout.get("font_pt") or cv_layout.get("font_used")}


def save_cv_document(cv_id: int, data: dict) -> str:
    from . import db

    _, tailored, _ = db.get_cv(cv_id)
    if data.get("rev") and data["rev"] != revision(tailored.cv):
        raise Conflict("This CV was changed somewhere else. Reload the editor to get the new version.")
    cv_layout, _ = db.cv_layout(cv_id)
    layout = load_layout()
    cv, reg, kinds = from_content(data["content"], tailored.cv, layout, cv_layout.get("editor_ids", {}))
    tailored.cv = CVContent.model_validate(cv.model_dump(include=set(CVContent.model_fields)))
    # Text size belongs to this CV (it is fitted to one page, in data["fontPt"]); the rest of the style is shared.
    font = data.get("fontPt")
    layout["style"] = {**layout["style"], **data["style"]}
    if isinstance(data.get("design"), dict):
        layout["design"] = data["design"]
    _keep_layout(layout, data, layout["ids"], layout["contact"])
    save_layout(layout)
    new_layout = {**cv_layout, "editor_ids": reg, "editor_variant": data["variants"][0], "pdf_stale": True,
                  "editor_contact": kinds}
    if font and font != (cv_layout.get("font_pt") or cv_layout.get("font_used")):
        new_layout["font_pt"] = font
    db.update_cv(cv_id, tailored, new_layout)
    db.cache_delete(f"cvrate:{cv_id}:profile")
    return revision(tailored.cv)


# --- print: the React page as one self-contained HTML file -----------------------------------------------------------


def _bundle() -> tuple[str, str]:
    js, css = STATIC / "editor.js", STATIC / "editor.css"
    if not js.exists():
        raise RuntimeError("The CV editor is not built: run `npm install && npm run build` in the editor/ folder.")
    return js.read_text(), css.read_text() if css.exists() else ""


def image_bytes(url: str) -> tuple[bytes, str] | None:
    """The file behind an image URL the editor uses (the profile photo or an upload)."""
    if not url:
        return None
    if url.startswith("/editor/api/photo"):
        path = settings.PHOTO_PATH
    elif url.startswith("/editor/uploads/"):
        path = uploads_dir() / Path(url.split("?")[0]).name
    else:
        return None
    if not path.is_file():
        return None
    data = path.read_bytes()
    kind = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:2] == b"\xff\xd8" else (mimetypes.guess_type(path.name)[0] or "image/png")
    return data, kind


def _data_uri(url: str) -> str:
    found = image_bytes(url)
    return f"data:{found[1]};base64,{base64.b64encode(found[0]).decode()}" if found else ""


def _inline_images(content: dict) -> dict:
    content = copy.deepcopy(content)
    content["photo"] = _data_uri(content["photo"])
    for j in content["experience"]:
        j["logo"] = _data_uri(j["logo"])
    return content


def _inline_design(design: dict, hidden: list[str]) -> dict:
    """The design with the images of free elements inlined (and the ones this version hides left out)."""
    design = copy.deepcopy(design)
    design["free"] = [f for f in design.get("free", []) if f.get("id") not in hidden]
    for f in design["free"]:
        if f.get("type") == "image":
            f["src"] = _data_uri(f.get("src", ""))
    return design


def fonts_link(*parts: object) -> str:
    """The Google Fonts stylesheet for the fonts these style/design objects use (empty when none is needed)."""
    names = sorted(set(re.findall(r"'([^']+)'", json.dumps(parts))) & GOOGLE_FONTS)
    if not names:
        return ""
    families = "&".join(f"family={n.replace(' ', '+')}:wght@400;700" for n in names)
    return f'<link href="https://fonts.googleapis.com/css2?{families}&display=swap" rel="stylesheet">'


def list_uploads() -> list[str]:
    """Images uploaded in the editor, newest first."""
    folder = uploads_dir()
    if not folder.is_dir():
        return []
    files = [f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXT and f.is_file()]
    return [f"/editor/uploads/{f.name}" for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)]


def print_html(cv: CVContent, variant: dict | None = None, font_pt: float | None = None, cv_ids: dict | None = None) -> str:
    """A CV as print-ready HTML, drawn by the editor's own page code."""
    layout = load_layout()
    content, _ = to_content(cv, layout, cv_ids if cv_ids is not None else layout["ids"])
    style = {**layout["style"], **({"fontSize": font_pt} if font_pt else {})}
    variant = variant or default_cv_variant(layout, "CV")
    design = _inline_design(layout["design"], variant.get("hidden", []))
    data = {"mode": "print", "content": _inline_images(content), "variants": [variant], "style": style, "design": design}
    js, css = _bundle()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    font_link = fonts_link(style, layout["design"])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_esc(cv.name)} - CV</title>{font_link}'
            f"<style>{css}</style></head><body><div id=\"root\"></div>"
            f"<script>window.__CV_PRINT__ = {payload};</script>"
            f'<script type="module">{js.replace("</script", "<\\/script")}</script></body></html>')


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def font_size() -> float:
    return float(load_layout()["style"]["fontSize"])


def safe_file_name(name: str) -> str:
    return re.sub(r"\s+", "_", re.sub(r"[^\w. -]+", "_", name)).strip("._")[:120] or "CV"


# --- uploads ---------------------------------------------------------------------------------------------------------

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def save_upload(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXT:
        raise ValueError("Unsupported image type")
    uploads_dir().mkdir(parents=True, exist_ok=True)
    stored = f"{int(time.time() * 1000)}-{_slug(Path(name).stem) or 'image'}{ext}"
    (uploads_dir() / stored).write_bytes(data)
    return f"/editor/uploads/{stored}"


# --- one-time import from the CVEditor project -----------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w+#]+", " ", text.lower()).split())


def _best(text: str, candidates: list[str], used: set[int], threshold: float) -> int | None:
    scores = [(SequenceMatcher(None, _norm(text), _norm(c)).ratio(), i) for i, c in enumerate(candidates) if i not in used]
    if not scores:
        return None
    score, i = max(scores)
    return i if score >= threshold else None


def import_cveditor(path: Path = CVEDITOR_DATA) -> list[str]:
    """Bring the CVEditor's versions, style and images in, keeping each version looking as it did.

    Content goes into the profile without losing anything: bullets, projects, skill groups and the like that only
    the CVEditor had are added; profile items the CVEditor didn't show are hidden in the imported versions; where
    the wording differs, the version keeps the CVEditor's wording for itself. Returns a report of what changed."""
    data = json.loads(path.read_text())
    ec, src_root = data["content"], path.parent.parent / "public"
    profile = assistant.load_profile()
    if not profile:
        raise ValueError("Set up your profile first (My profile → Import my CV).")
    backup = settings.PROFILE_PATH.with_name(f"profile.before-cveditor-import-{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy(settings.PROFILE_PATH, backup)
    report = [f"Your profile before the import is saved as {backup.name}."]

    def copy_image(url: str) -> str:
        src = src_root / url.lstrip("/")
        return save_upload(src.name, src.read_bytes()) if url and src.is_file() else ""

    # Each entry: what the imported versions show for one profile item (by index), and wording kept per version.
    hidden_keys: list[tuple[str, int, int | None]] = []   # (kind, item index, bullet index or None)
    overrides: list[tuple[str, int, int | None, str]] = []
    job_map: dict[str, int] = {}
    proj_map: dict[str, int] = {}
    logos: dict[int, str] = {}

    def reconcile(target_bullets: list[str], wanted: list[str], kind: str, index: int, where: str) -> list[str]:
        """Order the profile's bullets as the CVEditor shows them; returns the new bullet list."""
        used: set[int] = set()
        shown: list[tuple[int, str | None]] = []  # (index in target_bullets, own wording)
        added = []
        for text in wanted:
            exact = next((i for i, b in enumerate(target_bullets) if i not in used and _norm(b) == _norm(text)), None)
            hit = exact if exact is not None else _best(text, target_bullets, used, 0.55)
            if hit is None:
                target_bullets = [*target_bullets, text]
                hit = len(target_bullets) - 1
                added.append(text)
            used.add(hit)
            shown.append((hit, None if target_bullets[hit] == text else text))
        rest = [i for i in range(len(target_bullets)) if i not in used]
        order = [i for i, _ in shown] + rest
        for new_pos, (_, own) in enumerate(shown):
            if own:
                overrides.append((kind, index, new_pos, own))
        for new_pos in range(len(shown), len(order)):
            hidden_keys.append((kind, index, new_pos))
        if added:
            report.append(f"{where}: added {len(added)} bullet(s) from the CVEditor to your profile.")
        if rest:
            report.append(f"{where}: {len(rest)} profile bullet(s) are hidden in the imported versions.")
        reworded = sum(1 for _, own in shown if own)
        if reworded:
            report.append(f"{where}: {reworded} bullet(s) keep the CVEditor's wording in the imported versions.")
        return [target_bullets[i] for i in order]

    # Jobs
    for ej in ec["experience"]:
        i = next((k for k, e in enumerate(profile.experience) if _norm(e.company) == _norm(ej["company"])), None)
        if i is None:
            start, end = _split_dates(ej["dates"])
            profile.experience.append(Experience(title=ej["role"], company=ej["company"], start=start, end=end, location="", bullets=[]))
            i = len(profile.experience) - 1
            report.append(f"Added the job {ej['role']} at {ej['company']} to your profile.")
        job = profile.experience[i]
        job.bullets = reconcile(job.bullets, ej["bullets"], "job", i, f"{job.title} at {job.company}")
        job_map[ej["id"]] = i
        if ej.get("logo"):
            logos[i] = copy_image(ej["logo"])
    for i in range(len(profile.experience)):
        if i not in job_map.values():
            hidden_keys.append(("job", i, None))

    # Projects
    for ep in ec["projects"]:
        names = [p.name for p in profile.projects]
        i = next((k for k, n in enumerate(names) if _norm(n) == _norm(ep["title"])), None)
        if i is None:
            i = _best(ep["title"], names, set(proj_map.values()), 0.7)
        if i is None:
            profile.projects.append(Project(name=ep["title"], tech=_tech_store(ep["tech"]), link="", bullets=[]))
            i = len(profile.projects) - 1
            report.append(f"Added the project {ep['title']} to your profile.")
        proj = profile.projects[i]
        if _norm(_tech_store(ep["tech"])) != _norm(proj.tech):
            overrides.append(("tech", i, None, ep["tech"]))
        if ep["title"] != proj.name:
            report.append(f"Project {proj.name}: the CVEditor calls it “{ep['title']}”; the profile's name is used.")
        proj.bullets = reconcile(proj.bullets, ep["bullets"], "proj", i, proj.name)
        proj_map[ep["id"]] = i
    for i in range(len(profile.projects)):
        if i not in proj_map.values():
            hidden_keys.append(("proj", i, None))

    # Skill groups
    shown_groups = set()
    for es in ec["skills"]:
        i = next((k for k, g in enumerate(profile.skills) if _norm(g.category) == _norm(es["label"])), None)
        if i is None:
            profile.skills.append(SkillGroup(category=es["label"], items=split_items(es["value"])))
            i = len(profile.skills) - 1
            report.append(f"Added the skill group {es['label']} to your profile.")
        elif _norm(", ".join(profile.skills[i].items)) != _norm(es["value"]):
            overrides.append(("skill", i, None, es["value"]))
            # "C++" is already covered by "C++ (C++14)"
            have = {_norm(y) for y in profile.skills[i].items} | {_norm(y.split(" (")[0]) for y in profile.skills[i].items}
            missing = [x for x in split_items(es["value"]) if _norm(x) not in have]
            if missing:
                profile.skills[i].items += missing
                report.append(f"Skills · {es['label']}: added {', '.join(missing)} to your profile.")
        shown_groups.add(i)
    for i in range(len(profile.skills)):
        if i not in shown_groups:
            hidden_keys.append(("skill", i, None))
    if len(profile.skills) > len(shown_groups):
        report.append(f"Skills: {len(profile.skills) - len(shown_groups)} profile skill group(s) are hidden in the imported versions; "
                      "the shown groups keep the CVEditor's shorter lists.")

    # Education
    for ee in ec["education"]:
        i = next((k for k, e in enumerate(profile.education) if _norm(e.school) == _norm(ee["school"])), None)
        if i is None:
            profile.education.append(Education(degree=ee["degree"], school=ee["school"], years=""))
            report.append(f"Added {ee['degree']} at {ee['school']} to your profile.")
        elif _norm(profile.education[i].degree) != _norm(ee["degree"]):
            overrides.append(("degree", i, None, ee["degree"]))
            report.append(f"Education: the imported versions show “{ee['degree']}”; your profile keeps “{profile.education[i].degree}”.")

    # Short lists: anything only the CVEditor had is added.
    for key, attr in (("softSkills", "soft_skills"), ("interests", "interests")):
        have = {_norm(x) for x in getattr(profile, attr)}
        extra = [x for x in ec.get(key, []) if _norm(x) not in have]
        if extra:
            setattr(profile, attr, [*getattr(profile, attr), *extra])
            report.append(f"{key}: added {', '.join(extra)} to your profile.")

    # Ids for the final profile, then the versions.
    layout = load_layout()
    layout["ids"] = {}
    layout["style"] = {**DEFAULT_STYLE, **data.get("style", {})}
    layout["headings"] = {**DEFAULT_HEADINGS, **ec.get("headings", {})}
    layout["projectsLink"] = ec.get("projectsLink", layout["projectsLink"])
    layout["contact"] = [_contact_kind(x) for x in ec.get("contact", []) if x.strip()]
    for line in ec.get("contact", []):
        kind = _contact_kind(line)
        if not kind.startswith("text:") and not getattr(profile.contact, kind):
            setattr(profile.contact, kind, line.strip())
    if ec.get("photo"):
        layout["photo"] = copy_image(ec["photo"])
    content, reg = to_content(profile, {**layout, "logos": {}}, {})
    layout["ids"] = reg
    layout["logos"] = {content["experience"][i]["id"]: url for i, url in logos.items() if url}

    def item_id(kind: str, i: int, b: int | None) -> str:
        group = {"job": "experience", "proj": "projects", "skill": "skills", "degree": "education", "tech": "projects"}[kind]
        item = content[group][i]
        return item["bullets"][b]["id"] if b is not None else item["id"]

    common_hidden = [item_id(k, i, b) for k, i, b in hidden_keys]
    common_overrides = {}
    for kind, i, b, text in overrides:
        key = f"b:{item_id(kind, i, b)}" if b is not None else f"{kind}:{item_id(kind, i, None)}"
        common_overrides[key] = text

    variants = []
    for ev in data["variants"]:
        own = dict(common_overrides)
        summary = ev.get("summary") if ev.get("summary") is not None else ec["summary"]
        if summary != profile.summary:
            own["summary"] = summary
        title = ev.get("title") if ev.get("title") is not None else ec["title"]
        if title != profile.headline:
            own["title"] = title
        mapped_hidden = []
        for h in ev.get("hidden", []):
            if h in MAIN_SECTIONS or h in SIDE_SECTIONS:
                mapped_hidden.append(h)
            elif h in job_map:
                mapped_hidden.append(content["experience"][job_map[h]]["id"])
            elif h in proj_map:
                mapped_hidden.append(content["projects"][proj_map[h]]["id"])
            else:
                es = next((s for s in ec["skills"] if s["id"] == h), None)
                g = next((k for k, gr in enumerate(profile.skills) if es and _norm(gr.category) == _norm(es["label"])), None)
                if g is not None:
                    mapped_hidden.append(content["skills"][g]["id"])
        side = list(ev.get("sideOrder", []))
        side.insert(side.index("softSkills") + 1 if "softSkills" in side else len(side), "languages")
        variants.append({
            "id": ev["id"], "name": ev["name"], "fileName": ev.get("fileName", ev["name"]), "location": ev.get("location", ""),
            "overrides": own, "mainOrder": ev.get("mainOrder", MAIN_SECTIONS), "sideOrder": side,
            "projectOrder": [content["projects"][proj_map[p]]["id"] for p in ev.get("projectOrder", []) if p in proj_map],
            "hidden": sorted(set(common_hidden + mapped_hidden + (["languages"] if not ec.get("languages") else []))),
        })
    layout["variants"] = variants
    layout["imported_at"] = time.time()
    if "summary" in common_overrides or any("summary" in v["overrides"] for v in variants):
        report.append("Summary: the imported versions keep the CVEditor's summary; your profile keeps its own.")
    if not ec.get("languages") and profile.languages:
        report.append("Languages: the CVEditor had no languages section, so it is hidden in the imported versions (show it in the left panel).")
    assistant.save_profile(profile)
    save_layout(layout)
    report.append(f"Imported {len(variants)} version(s): {', '.join(v['name'] for v in variants)}.")
    return report
