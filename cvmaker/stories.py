"""The story bank: STAR+R stories (situation, task, action, result, reflection) for behavioural interview
questions, kept per person in stories.json. Claude can draft them from the profile and from interview debriefs;
you edit and complete them. Interview prep, practice and debriefs all read the bank."""

import json
import re
import time

from . import settings
from .models import DraftStory, Story


def _path():
    return settings.DATA_DIR / "stories.json"


def load() -> list[Story]:
    if not _path().exists():
        return []
    return [Story.model_validate(s) for s in json.loads(_path().read_text())]


def save_all(items: list[Story]) -> None:
    _path().write_text(json.dumps([s.model_dump() for s in items], indent=2, ensure_ascii=False))


def get(story_id: str) -> Story | None:
    return next((s for s in load() if s.id == story_id), None)


def _new_id(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "story"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


def upsert(story: Story) -> Story:
    items = load()
    if not story.id:
        story.id = _new_id(story.title, {s.id for s in items})
    story.updated = time.time()
    found = False
    for i, s in enumerate(items):
        if s.id == story.id:
            items[i], found = story, True
    if not found:
        items.append(story)
    save_all(items)
    return story


def add_drafts(drafts: list[DraftStory], source: str) -> list[Story]:
    added = []
    for d in drafts:
        added.append(upsert(Story(**d.model_dump(), source=source)))
    return added


def delete(story_id: str) -> None:
    save_all([s for s in load() if s.id != story_id])


def incomplete(story: Story) -> bool:
    """Still has [placeholders] or open questions for the candidate."""
    text = " ".join([story.situation, story.task, story.action, story.result, story.reflection])
    return bool(story.fill_in) or "[" in text
