"""Role profiles: saved target positions that steer CV tailoring and prefill searches."""

import json
import re

from . import people, settings
from .models import RoleProfile


def _roles_path():
    return settings.DATA_DIR / "roles.json"


DEFAULT_ROLES = [
    RoleProfile(
        id="java-backend",
        name="Java Backend (Spring Boot)",
        headline="Java Backend Developer | Spring Boot, REST APIs & Microservices",
        focus=(
            "Position me as a backend engineer. Lead with the Spring Boot / microservices POS project and "
            "Java work, present the C++ experience as strong engineering fundamentals (performance, refactoring, "
            "architecture), and keep game-specific wording to a minimum."
        ),
        key_skills=["Java", "Spring Boot", "Spring Security", "REST API", "Microservices", "JWT"],
        role_keywords=["java", "backend", "back-end", "developer", "engineer"],
        skill_keywords=["java", "spring", "spring boot", "microservices", "rest"],
    ),
    RoleProfile(
        id="cpp-performance",
        name="C++ / Performance Engineer",
        headline="C++ Software Developer | Performance Optimization & Simulation Systems",
        focus=(
            "Position me as a C++ engineer who makes systems fast. Lead with the simulation engine optimization "
            "(~30-40K to ~13M games/sec), core game logic, refactoring and tooling. Keep the Java projects short."
        ),
        key_skills=["C++", "CMake", "Performance optimization", "Simulation", "Refactoring"],
        role_keywords=["c++", "developer", "engineer", "programmer"],
        skill_keywords=["c++", "performance", "simulation", "cmake", "gaming"],
    ),
]


def load_roles() -> list[RoleProfile]:
    if not _roles_path().exists():
        # The starter roles are written for the default person's background; others start with none.
        starters = list(DEFAULT_ROLES) if people.is_default() else []
        save_roles(starters)
        return starters
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
