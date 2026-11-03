"""Skill check for anything that goes to an employer: every technology a CV, letter or message mentions must
be backed by the profile (skills, experience, projects, interview facts, skill details) or by the candidate's
GitHub repos (language, topics, README, file names). Measured, no AI.

A flagged term is not necessarily wrong - "I haven't used Kafka yet" is honest - so each finding comes with the
sentence it appears in, and the page offers to add the skill to the profile when it is true."""

import re

from . import db, github
from .cvcheck import TECH_TERMS
from .models import Profile
from .search import _contains

# Words in TECH_TERMS that are too general to count as a claim, or that clash with plain English.
NOT_CLAIMS = {"english", "german", "performance", "security", "cloud", "agile", "algorithms", "data structures",
              "design patterns", "oop", "solid", "go", "rest", "unity", "spring", "integration testing",
              "unit testing", "concurrency", "multithreading", "distributed systems", "profiling", "embedded"}

EXTRA_TECH = [
    "golang", "rest api", "restful", "spring mvc", "spring cloud", "spring data", "quarkus", "micronaut",
    "django", "flask", "fastapi", "pandas", "numpy", "pytorch", "tensorflow", "next.js", "nestjs", "express.js",
    "svelte", "tailwind", "redux", "webpack", "vite", "objective-c", "php", "laravel", "ruby", "ruby on rails",
    "perl", "matlab", "dart", "flutter", "android", "ios", "sqlite", "mariadb", "cassandra", "dynamodb",
    "firebase", "snowflake", "bigquery", "apache spark", "hadoop", "airflow", "dbt", "kinesis", "pulsar", "nats",
    "zeromq", "protobuf", "openapi", "swagger", "soap", "websocket", "helm", "argo cd", "argocd", "ansible",
    "puppet", "prometheus", "grafana", "datadog", "splunk", "elk", "kibana", "logstash", "nginx",
    "apache", "tomcat", "jboss", "wildfly", "keycloak", "openshift", "eks", "aks", "gke", "aws lambda", "s3",
    "ec2", "cloudformation", "pulumi", "bitbucket", "teamcity", "bamboo", "circleci", "travis", "sonarqube",
    "selenium", "cypress", "playwright", "jest", "pytest", "gtest", "googletest", "catch2", "boost", "cuda",
    "opencl", "vulkan", "directx", "unreal engine", "unity3d", "sfml", "sdl", "assembly", "rtos", "freertos",
    "yocto", "fpga", "verilog", "vhdl", "blockchain", "solidity", "llm", "openai", "langchain",
    "microservice", "event sourcing", "cqrs", "ddd", "domain-driven design", "kanban",
    "powershell", "zsh", "windows server", "active directory", "vmware", "hyper-v", "valgrind", "gdb",
    "vtune", "clang", "gcc", "msvc", "visual studio", "intellij", "eclipse", "xcode", "figma",
]

# What else counts as evidence for a term (other spellings, or files that prove it in a repo).
ALIASES = {
    "golang": ["go"],
    "node.js": ["node", "nodejs", "package.json"],
    "javascript": ["js", "node", "typescript"],
    "typescript": ["ts", "tsconfig.json"],
    "postgresql": ["postgres", "psql"],
    "kubernetes": ["k8s", "kubectl", "helm"],
    "c#": ["csharp", ".csproj", ".net"],
    ".net": ["dotnet", "c#", ".csproj"],
    "c++": ["cpp", "c++14", "c++17", "c++20", "cmakelists.txt"],
    "ci/cd": ["ci", "continuous integration", "github actions", "jenkins", "gitlab ci", ".github/workflows", ".gitlab-ci.yml", "jenkinsfile"],
    "github actions": [".github/workflows"],
    "docker": ["dockerfile", "docker-compose", "compose.yaml", "compose.yml"],
    "maven": ["pom.xml"],
    "gradle": ["build.gradle", "build.gradle.kts"],
    "cmake": ["cmakelists.txt"],
    "spring boot": ["spring-boot", "springboot"],
    "spring security": ["spring-security", "spring-boot-starter-security"],
    "rest api": ["rest", "restful", "rest apis"],
    "restful": ["rest", "rest api"],
    "microservice": ["microservices"],
    "jwt": ["json web token", "jjwt"],
    "jpa": ["hibernate", "spring data"],
    "hibernate": ["jpa"],
    "junit": ["junit5", "jupiter"],
    "gtest": ["googletest", "gtest"],
    "googletest": ["gtest"],
    "unreal engine": ["unreal", "ue4", "ue5"],
    "unity3d": ["unity"],
    "react": ["react.js", "reactjs"],
    "vue": ["vue.js", "vuejs"],
    "angular": ["angularjs"],
    "aws": ["amazon web services", "ec2", "s3", "lambda"],
    "gcp": ["google cloud"],
    "azure": ["microsoft azure"],
    "linux": ["ubuntu", "debian", "fedora", "centos", "red hat"],
    "bash": ["shell", ".sh"],
}


# A sentence that says you have NOT used something is honest, not a claim.
HONEST = re.compile(
    r"\b(?:not yet|haven't|have not|hasn't|never (?:used|worked)|no (?:professional |hands-on )?experience|new to me|"
    r"would be new|eager to learn|keen to learn|want to learn|willing to learn|currently learning|learning|"
    r"not worked|did not use|didn't use|unfamiliar|ramp up|pick up)\b",
    re.I,
)


def vocabulary() -> list[str]:
    terms = {t.lower() for t in TECH_TERMS + EXTRA_TECH} - NOT_CLAIMS
    # Longest first, so "spring boot" is found before a shorter term inside it.
    return sorted(terms, key=len, reverse=True)


def _repo_text(profile: Profile) -> str:
    """What the cached GitHub repos show (no network: the cache filled by GitHub checks and fit checks)."""
    try:
        user = github.username(profile)
    except Exception:
        return ""
    cached = db.cache_get(f"github:{user}", 24 * 365 * 5) if user else None
    if not cached:
        return ""
    parts = []
    for r in cached:
        parts += [r.get("name", ""), r.get("description", ""), r.get("language", ""), " ".join(r.get("topics", [])),
                  r.get("readme", ""), " ".join(r.get("files", [])), " ".join(r.get("build_files", {}) or {}),
                  " ".join((r.get("build_files") or {}).values())]
    return "\n".join(parts)


def evidence_text(profile: Profile) -> str:
    return (profile.model_dump_json() + "\n" + _repo_text(profile)).lower().replace("**", "")


def _backed(term: str, evidence: str) -> bool:
    return any(_contains(evidence, t) or (("." in t or "/" in t) and t in evidence) for t in [term, *ALIASES.get(term, [])])


def _sentence(text: str, start: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    ends = [i for i in (text.find(".", start), text.find("\n", start)) if i != -1]
    right = min(ends) if ends else len(text)
    return " ".join(text[left:right + 1].split()).strip()[:220]


def unsupported(text: str, profile: Profile | None, evidence: str | None = None, job=None) -> list[dict]:
    """Technologies the text mentions that the profile and GitHub don't show: [{term, sentence}].
    Naming the job you apply for ("the Kotlin Developer role at Oracle") is not a claim, so the job's title and
    company are blanked out before looking."""
    if not profile or not text:
        return []
    evidence = evidence if evidence is not None else evidence_text(profile)
    plain = text.replace("**", "")
    low = plain.lower()
    if job is not None:
        for name in (job.title, job.company):
            if name.strip():
                low = re.sub(re.escape(name.lower().strip()), lambda m: " " * len(m.group(0)), low)
    found, covered = [], []
    for term in vocabulary():
        if not _contains(low, term):
            continue
        # "spring boot" already found: don't report "spring" (or "boot") again from the same words.
        if any(term != c and _contains(c, term) for c in covered):
            continue
        covered.append(term)
        if _backed(term, evidence):
            continue
        m = re.search(re.escape(term), low)
        sentence = _sentence(plain, m.start() if m else 0)
        label = plain[m.start():m.end()] if m else term  # the spelling used in the text, e.g. "Terraform"
        found.append({"term": label, "sentence": sentence, "honest": bool(HONEST.search(sentence))})
    return found


def problems(findings: list[dict]) -> list[dict]:
    """The findings that read as a claim (not an honest 'I haven't used X yet')."""
    return [f for f in findings if not f["honest"]]


def check_many(texts: dict[str, str], profile: Profile | None, job=None) -> dict[str, list[dict]]:
    """The same check for several drafts at once (the evidence is built once)."""
    if not profile:
        return {}
    evidence = evidence_text(profile)
    return {k: unsupported(v, profile, evidence, job) for k, v in texts.items() if v}


def add_skill(profile: Profile, term: str) -> None:
    """The candidate confirmed it: add the skill to the profile (an 'Added by you' group)."""
    from .models import SkillGroup

    label = term.strip()
    if not label:
        return
    if any(i.lower() == label.lower() for g in profile.skills for i in g.items):
        return
    group = next((g for g in profile.skills if g.category == "Added by you"), None)
    if group is None:
        group = SkillGroup(category="Added by you", items=[])
        profile.skills.append(group)
    group.items.append(label)

