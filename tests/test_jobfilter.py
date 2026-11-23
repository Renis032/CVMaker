"""The Jobs page filters: the text query, each filter, the counts beside the options, and paging."""

import time

from starlette.datastructures import QueryParams

from cvmaker import jobfilter
from cvmaker.models import Job


def _row(i, title, company="Acme", location="Sofia, Bulgaria", remote=False, tags=(), description="", fit=None, score=50,
         pay=None, source="dev.bg", age_days=1.0):
    return {
        "job": Job(id=f"j{i}", source=source, company=company, title=title, location=location, remote=remote,
                   tags=list(tags), description=description),
        "fit": fit, "score": score, "salary_month": pay, "first_seen": time.time() - age_days * 86400,
    }


ROWS = [
    _row(1, "Senior Java Developer", tags=["java", "spring"], fit=8, pay=4000, description="We use Spring Boot and Kafka."),
    _row(2, "Junior Python Developer", company="Bluefin", location="Remote", remote=True, tags=["python"], fit=4,
         source="remoteok", age_days=10),
    _row(3, "Backend Engineer", company="Helix Data", location="Berlin, Germany", tags=["hybrid", "java"], pay=6000,
         description="Java services on AWS.", age_days=40),
    _row(4, "Data Engineer", company="Acme", location="Lisbon", tags=["python", "aws"], fit=6, score=20),
]


def _apply(query: str, rows=ROWS):
    f = jobfilter.Filters.from_params(QueryParams(query))
    return f, jobfilter.apply(rows, f)


def _ids(result):
    return [r["job"].id for r in result.rows]


def test_words_must_all_match_and_search_the_description():
    assert _ids(_apply("q=java")[1]) == ["j1", "j3"]  # title hit ranks above a tag/description hit
    assert _ids(_apply("q=java+aws")[1]) == ["j3"]
    assert _ids(_apply("q=kafka")[1]) == ["j1"]


def test_phrases_negation_and_fields():
    assert _ids(_apply('q="spring boot"')[1]) == ["j1"]
    assert _ids(_apply("q=developer+-senior")[1]) == ["j2"]
    assert sorted(_ids(_apply("q=company:acme")[1])) == ["j1", "j4"]
    assert _ids(_apply("q=skill:python+location:remote")[1]) == ["j2"]


def test_snippet_marks_a_word_found_only_in_the_description():
    row = _apply("q=kafka")[1].rows[0]
    assert "<mark>Kafka</mark>" in row["snippet"]
    assert _apply("q=java")[1].rows[0]["snippet"] is None  # visible in the title already
    hostile = [_row(9, "Dev", description="<script>alert(1)</script> kafka")]
    assert "<script>" not in _apply("q=kafka", hostile)[1].rows[0]["snippet"]


def test_each_filter():
    assert _ids(_apply("where=remote")[1]) == ["j2"]
    assert _ids(_apply("where=hybrid&where=remote")[1]) == ["j2", "j3"]
    assert _ids(_apply("level=senior")[1]) == ["j1"]
    assert _ids(_apply("country=DE")[1]) == ["j3"]
    assert _ids(_apply("country=other")[1]) == ["j2", "j4"]
    assert _ids(_apply("fit=6")[1]) == ["j1", "j4"]
    assert _ids(_apply("rated=no")[1]) == ["j3"]
    assert _ids(_apply("pay=5000")[1]) == ["j3"]
    assert _ids(_apply("days=7")[1]) == ["j1", "j4"]
    assert _ids(_apply("score=30")[1]) == ["j1", "j2", "j3"]
    assert _ids(_apply("tag=python&tag=aws")[1]) == ["j4"]
    assert _ids(_apply("company=helix++data")[1]) == ["j3"]
    assert _ids(_apply("source=remoteok")[1]) == ["j2"]


def test_counts_ignore_the_facets_own_choice():
    _, result = _apply("where=remote")
    counts = {o["value"]: o["count"] for o in result.facets["where"]}
    assert counts == {"remote": 1, "hybrid": 1, "onsite": 2}
    # ...but follow every other filter
    _, result = _apply("where=remote&level=senior")
    assert {o["value"]: o["count"] for o in result.facets["where"]} == {"remote": 0, "onsite": 1}
    assert result.total == 0


def test_paging_and_urls():
    rows = [_row(i, f"Developer {i}") for i in range(120)]
    f, result = _apply("where=onsite", rows)
    assert (result.shown, result.total) == (50, 120)
    assert result.more_url == "/?where=onsite&limit=100"
    assert f.url(where=["remote"]) == "/?where=remote"  # changing a filter starts again from the first page
    f, _ = _apply("q=java&where=remote&where=hybrid&fit=7&status=liked")
    assert f.without("where", "remote") == "/?status=liked&q=java&fit=7&where=hybrid"
    assert f.cleared() == "/?status=liked"
    assert [label for label, _ in f.chips()] == ["“java”", "Remote", "Hybrid", "Fit 7+"]
    assert f.narrowing == 4


def test_bad_parameters_are_ignored():
    f = jobfilter.Filters.from_params(QueryParams("fit=abc&where=mars&sort=;drop&limit=-5&status=applied&pay=x"))
    assert (f.fit, f.where, f.sort, f.limit, f.status, f.pay) == (0, [], "", jobfilter.PAGE_SIZE, "", 0)


def test_suggest():
    out = jobfilter.suggest(ROWS, "acme")
    assert out["companies"] == [{"name": "Acme", "count": 2}]
    assert {j["id"] for j in out["jobs"]} == {"j1", "j4"}
