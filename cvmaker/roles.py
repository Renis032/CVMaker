"""Role profiles: saved target positions that steer CV tailoring and prefill searches."""

import json
import re

from . import settings
from .models import RoleProfile


def _roles_path():
    return settings.DATA_DIR / "roles.json"


def load_roles() -> list[RoleProfile]:
    if not _roles_path().exists():
        return []  # no role profiles until you make one (the job search has job categories to start from)
    return [RoleProfile.model_validate(r) for r in json.loads(_roles_path().read_text())]


def save_roles(roles: list[RoleProfile]) -> None:
    _roles_path().write_text(json.dumps([r.model_dump() for r in roles], indent=2, ensure_ascii=False))


def get_role(role_id: str) -> RoleProfile | None:
    return next((r for r in load_roles() if r.id == role_id), None)


def upsert_role(role: RoleProfile) -> RoleProfile:
    roles = load_roles()
    if not role.id:
        base = re.sub(r"[^a-z0-9]+", "-", role.name.lower()).strip("-") or "role"
        taken = {r.id for r in roles}
        role.id = base
        n = 2
        while role.id in taken:
            role.id = f"{base}-{n}"
            n += 1
    roles = [r for r in roles if r.id != role.id] + [role]
    save_roles(roles)
    return role


def delete_role(role_id: str) -> None:
    save_roles([r for r in load_roles() if r.id != role_id])
