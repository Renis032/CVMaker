"""What a tailored CV changed compared with the profile it came from, and a check for facts not in the profile.

All measured, no AI: bullets are matched by text similarity, words are diffed, and skills and numbers in the
tailored CV are looked up in the full profile (including interview facts and skill details).
"""

import re
from difflib import SequenceMatcher

from markupsafe import Markup, escape

from . import claims, cvcheck
from .models import CVContent, Profile

# Below this similarity a bullet counts as new rather than reworded.
REWORD_MIN_RATIO = 0.45


def _plain(text: str) -> str:
    return " ".join(re.sub(r"\*\*", "", text).split())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _plain(a).lower(), _plain(b).lower()).ratio()


def word_diff(old: str, new: str) -> Markup:
    """The new text with removed words struck through and added words highlighted."""
    a, b = _plain(old).split(), _plain(new).split()
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        if op == "equal":
            out.append(escape(" ".join(a[i1:i2])))
            continue
        if op in ("replace", "delete"):
            out.append(Markup("<del>%s</del>") % " ".join(a[i1:i2]))
        if op in ("replace", "insert"):
            out.append(Markup("<ins>%s</ins>") % " ".join(b[j1:j2]))
    return Markup(" ").join(out)


def _match_bullets(old: list[str], new: list[str]) -> tuple[list[dict], list[str]]:
    """Pair each new bullet with its most similar unused old bullet. Returns (new bullets, dropped old ones)."""
    pairs = sorted(
        ((_ratio(o, n), oi, ni) for oi, o in enumerate(old) for ni, n in enumerate(new)), reverse=True
    )
    used_old, matched = set(), {}
    for ratio, oi, ni in pairs:
        if ratio < REWORD_MIN_RATIO:
            break
        if oi in used_old or ni in matched:
            continue
        used_old.add(oi)
        matched[ni] = (oi, ratio)
    bullets = []
    for ni, n in enumerate(new):
        if ni not in matched:
            bullets.append({"kind": "new", "text": n})
            continue
        oi, ratio = matched[ni]
        same = _plain(old[oi]).lower() == _plain(n).lower()
        bullets.append({
            "kind": "same" if same else "reworded",
            "text": n,
            "diff": None if same else word_diff(old[oi], n),
            "moved": oi != ni,
        })
    dropped = [o for oi, o in enumerate(old) if oi not in used_old]
    return bullets, dropped


def _key(*parts: str) -> str:
    return " ".join(" ".join(parts).lower().split())


def _sections(old_items, new_items, key, bullets) -> dict:
    old_by_key = {key(o): o for o in old_items}
    new_keys = [key(n) for n in new_items]
    entries = []
    for n in new_items:
        o = old_by_key.get(key(n))
        if o is None:
            entries.append({"item": n, "added": True, "bullets": [{"kind": "new", "text": b} for b in bullets(n)], "dropped": []})
            continue
        matched, dropped = _match_bullets(bullets(o), bullets(n))
        entries.append({"item": n, "added": False, "bullets": matched, "dropped": dropped})
    old_order = [key(o) for o in old_items if key(o) in new_keys]
    return {
        "entries": entries,
        "removed": [o for o in old_items if key(o) not in new_keys],
        "reordered": old_order != [k for k in new_keys if k in old_by_key],
    }


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?\s?%?\+?", text.replace(" ", " ")))


def diff(profile: Profile, cv: CVContent, job=None) -> dict:
    base = CVContent.model_validate(profile.model_dump(include=set(CVContent.model_fields)))
    experience = _sections(base.experience, cv.experience, lambda r: _key(r.title, r.company), lambda r: r.bullets)
    projects = _sections(base.projects, cv.projects, lambda p: _key(p.name), lambda p: p.bullets)

    old_skills = {i.lower(): i for g in base.skills for i in g.items}
    new_skills = {i.lower(): i for g in cv.skills for i in g.items}

    # Fact check: everything the CV says must be findable somewhere in the profile.
    profile_text = _plain(profile.model_dump_json()).lower()
    unsupported_skills = [v for k, v in new_skills.items() if k not in old_skills and k not in profile_text]
    cv_texts = [cv.headline, cv.summary] + [b for r in cv.experience for b in r.bullets] + [b for p in cv.projects for b in p.bullets]
    cv_texts += [b for sec in cv.sections for e in sec.entries for b in e.bullets]
    profile_numbers = {n.replace(" ", "").rstrip("+%") for n in _numbers(profile_text)}
    unsupported_numbers = []
    for text in cv_texts:
        for n in _numbers(_plain(text)):
            if n.replace(" ", "").rstrip("+%") not in profile_numbers:
                unsupported_numbers.append((n.strip(), _plain(text)))

    counts = {"same": 0, "reworded": 0, "new": 0, "dropped": 0}
    for section in (experience, projects):
        for e in section["entries"]:
            for b in e["bullets"]:
                counts[b["kind"]] += 1
            counts["dropped"] += len(e["dropped"])
        counts["dropped"] += sum(len(r.bullets) for r in section["removed"])

    return {
        "headline": (base.headline, cv.headline) if _plain(base.headline) != _plain(cv.headline) else None,
        "summary": word_diff(base.summary, cv.summary) if _plain(base.summary) != _plain(cv.summary) else None,
        "experience": experience,
        "projects": projects,
        "skills_added": [v for k, v in new_skills.items() if k not in old_skills],
        "skills_removed": [v for k, v in old_skills.items() if k not in new_skills],
        "soft_removed": [s for s in base.soft_skills if s not in cv.soft_skills],
        "unsupported_skills": unsupported_skills,
        "unsupported_numbers": unsupported_numbers,
        # Technologies named anywhere in the CV (not only the skills list) that the profile and GitHub don't show.
        "unsupported_tech": [f for f in claims.unsupported(cvcheck.cv_text(cv), profile, job=job)
                             if f["term"].lower() not in {x.lower() for x in unsupported_skills}],
        "counts": counts,
    }
