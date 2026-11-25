"""LinkedIn data export: the CSVs read as written, and added to a profile that's already there without losing
anything the person wrote themselves."""

import io
import zipfile

import pytest
from conftest import new_user
from helpers import make_profile

from cvmaker import assistant, linkedin, people

FILES = {
    "Profile.csv": "﻿First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry,Zip Code,Geo Location,Twitter Handles,Websites,Instant Messengers\n"
                   'Alex,Morgan,,,,"Backend Engineer | Java, Kotlin","I build payment systems.",Software,,"Sofia, Bulgaria",,"[PORTFOLIO:https://alex.dev,OTHER:https://github.com/alexm]",\n',
    "Positions.csv": "Company Name,Title,Description,Location,Started On,Finished On\n"
                     'Northwind,Senior Java Developer,"- Led the move to Kotlin\n- Cut p99 latency by 40%","Sofia, Bulgaria",Jun 2025,\n'
                     "Acme,Java Developer,Built REST services,Sofia,Jan 2021,May 2025\n"
                     "Initech,Intern,,Plovdiv,Jun 2019,Sep 2019\n",
    "Education.csv": "School Name,Start Date,End Date,Notes,Degree Name,Activities\n"
                     "Sofia University,2016,2020,,BSc Computer Science,\n",
    "Skills.csv": "Name\nJava\nKotlin\nsql\nKubernetes\n",
    "Languages.csv": "Name,Proficiency\nEnglish,Full professional proficiency\nGerman,Elementary proficiency\n",
    "Certifications.csv": "Name,Url,Authority,Started On,Finished On,License Number\nAWS Solutions Architect,,Amazon Web Services,Mar 2024,,\n",
    "Email Addresses.csv": "Email Address,Confirmed,Primary,Updated On\nold@example.com,Yes,No,\nalex@linkedin-mail.example,Yes,Yes,\n",
    "Connections.csv": 'Notes:\n"When exporting your connection data, you may notice that some of the email addresses are missing."\n\n'
                       "First Name,Last Name,URL,Email Address,Company,Position,Connected On\nSam,Lee,,,,,\n",
    "Jobs/Job Seeker Preferences.csv": "Locations,Industries,Company Employee Count,Preferred Job Types,Job Titles,Open To Recruiters,Phone Number,Commute Preference Starting Address\n"
                                       '"Sofia, Bulgaria | Remote",Software Development,,Full-time | Contract,Backend Engineer | Java Developer,Yes,+359 1,Some street 1\n',
}


def export(files=FILES) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_new_profile_from_the_export():
    p, report, _ = linkedin.import_export(export(), None)
    assert p.name == "Alex Morgan" and p.headline == "Backend Engineer | Java, Kotlin"
    assert p.contact.location == "Sofia, Bulgaria"
    assert p.contact.email == "alex@linkedin-mail.example"          # the primary address
    assert p.contact.github == "https://github.com/alexm" and p.contact.other == ["https://alex.dev"]
    assert [e.company for e in p.experience] == ["Northwind", "Acme", "Initech"]
    assert p.experience[0].end == "Present"
    assert p.experience[0].bullets == ["Led the move to Kotlin", "Cut p99 latency by 40%"]
    assert p.experience[1].bullets == ["Built REST services"] and p.experience[2].bullets == []
    assert p.education[0].school == "Sofia University" and p.education[0].years == "2016 – 2020"
    assert p.skills[0].items == ["Java", "Kotlin", "sql", "Kubernetes"]
    assert p.languages == ["English (Full professional proficiency)", "German (Elementary proficiency)"]
    assert p.sections[0].title == "Certifications" and p.sections[0].entries[0].subtitle == "Amazon Web Services"
    assert "Job titles: Backend Engineer, Java Developer" in p.preferences
    assert "Locations: Sofia, Bulgaria, Remote" in p.preferences
    assert "Open to recruiters: yes" in p.preferences
    assert not any("+359" in x or "street" in x for x in p.preferences)   # contact details aren't preferences
    assert report[0].startswith("Profile created from LinkedIn: 3 jobs")


def test_merge_keeps_what_you_wrote_and_adds_the_rest():
    mine = make_profile(preferences=["Job titles: Old title", "Wants a team that pairs"])
    p, report, _ = linkedin.import_export(export(), mine)
    assert p.headline == "Java Developer" and p.summary == mine.summary      # your own wording stays
    assert p.contact.email == "alex@example.com"
    assert p.contact.github == "https://github.com/alexm"                     # was empty: filled in
    acme = next(e for e in p.experience if e.company == "Acme")
    assert acme.bullets == mine.experience[0].bullets                         # same job, your bullets kept
    assert [e.company for e in p.experience] == ["Northwind", "Acme", "Initech"]  # newest first
    all_skills = [s for g in p.skills for s in g.items]
    assert all_skills.count("Java") == 1 and "Kotlin" in all_skills and "sql" not in all_skills  # "SQL" already there
    assert p.languages == ["English", "German (Elementary proficiency)"]
    assert "Wants a team that pairs" in p.preferences and "Job titles: Old title" not in p.preferences
    assert any("Kept your own headline, summary" in line for line in report)
    assert any("Added 2 jobs" in line for line in report)


def test_importing_twice_adds_nothing_the_second_time():
    once, _, _ = linkedin.import_export(export(), make_profile())
    twice, report, _ = linkedin.import_export(export(), once)
    assert twice == once
    assert report[0] == "Nothing new: your profile already has everything in this LinkedIn export."


def test_rejects_zips_that_are_not_an_export():
    with pytest.raises(linkedin.LinkedInError):
        linkedin.read_export(export({"notes.csv": "a,b\n1,2\n"}))
    with pytest.raises(linkedin.LinkedInError):
        linkedin.read_export(b"not a zip")


def test_upload_route_saves_the_profile_and_shows_the_report():
    client, account = new_user()
    r = client.post("/profile/import-linkedin", files={"export": ("Basic_LinkedInDataExport.zip", export(), "application/zip")})
    assert r.status_code == 303 and r.headers["location"] == "/profile#linkedin"
    with people.in_account(account["id"]):
        assert assistant.load_profile().name == "Alex Morgan"
    page = client.get("/profile").text
    assert "Imported from LinkedIn" in page and "3 jobs" in page

    bad = client.post("/profile/import-linkedin", files={"export": ("cv.pdf", b"%PDF-1.4 nope", "application/pdf")})
    assert bad.status_code == 303 and "error=" in bad.headers["location"]


# --- job-seeker settings → saved search ---------------------------------------------------------------------


def test_wishes_from_the_job_seeker_settings():
    w = linkedin.wishes(linkedin.read_export(export()))
    assert w.titles == ["Backend Engineer", "Java Developer"]
    assert w.locations == ["Sofia, Bulgaria"] and w.remote


def test_search_changes_offer_only_what_the_search_lacks():
    from cvmaker.models import Criteria

    w = linkedin.Wishes(titles=["Java Developer", "Engineering Manager"], locations=["Berlin, Germany", "Sofia, Bulgaria", "Atlantis"], remote=True)
    c = Criteria(role_keywords=["java developer"], countries=["BG"], include_remote=False)
    ch = linkedin.search_changes(w, c)
    assert ch.titles == ["Engineering Manager"]               # "java developer" is already there
    assert ch.countries == ["DE"] and ch.not_covered == ["Atlantis"]
    assert ch.remote and ch.unexclude == ["manager"]          # the default exclude would hide the wanted title
    assert not ch.narrows                                     # the search already had title words

    after = linkedin.apply_changes(c, ch)
    assert after.role_keywords == ["java developer", "Engineering Manager"]
    assert after.countries == ["BG", "DE"] and after.include_remote and "manager" not in after.exclude
    assert linkedin.search_changes(w, after).empty()          # nothing left to offer
    assert linkedin.search_changes(w, Criteria()).narrows    # a search that takes any title would be limited


def test_offer_on_the_profile_page_and_apply_only_what_is_ticked():
    from cvmaker import auto

    client, account = new_user()
    client.post("/profile/import-linkedin", files={"export": ("export.zip", export(), "application/zip")})
    page = client.get("/profile").text
    assert "Use your LinkedIn job preferences in your job search?" in page
    assert 'value="Backend Engineer"' in page

    r = client.post("/profile/import-linkedin/search", data={"action": "apply", "titles": ["Backend Engineer", "Not offered"]})
    assert r.status_code == 303 and r.headers["location"].startswith("/?search=")
    with people.in_account(account["id"]):
        c = auto.active_search().criteria
        assert c.role_keywords == ["Backend Engineer"]           # only the ticked, offered title
        assert c.countries == ["BG"]                             # Bulgaria was already there
    assert "Use your LinkedIn job preferences" not in client.get("/profile").text   # the offer is gone


def test_save_all_as_a_new_search_and_skip():
    from cvmaker import auto

    client, account = new_user()
    client.post("/profile/import-linkedin", files={"export": ("export.zip", export(), "application/zip")})
    with people.in_account(account["id"]):
        before = auto.active_search().criteria
    client.post("/profile/import-linkedin/search", data={"action": "skip"})
    with people.in_account(account["id"]):
        assert auto.active_search().criteria == before and len(auto.load_searches()) == 1

    client.post("/profile/import-linkedin", files={"export": ("export.zip", export(), "application/zip")})
    client.post("/profile/import-linkedin/search", data={"action": "new"})
    with people.in_account(account["id"]):
        s = auto.active_search()
        assert s.name == "From LinkedIn" and len(auto.load_searches()) == 2
        assert s.criteria.role_keywords == ["Backend Engineer", "Java Developer"]
        assert s.criteria.countries == ["BG"] and s.criteria.include_remote


def test_upload_the_csv_files_from_an_unpacked_export():
    client, account = new_user()
    r = client.post("/profile/import-linkedin", files=[
        ("export", ("Profile.csv", FILES["Profile.csv"].encode(), "text/csv")),
        ("export", ("Positions.csv", FILES["Positions.csv"].encode(), "text/csv")),
    ])
    assert r.headers["location"] == "/profile#linkedin"
    with people.in_account(account["id"]):
        p = assistant.load_profile()
        assert p.name == "Alex Morgan" and len(p.experience) == 3
    page = client.get("/profile").text
    assert "Not in this export: Education, Skills, Job seeker preferences" in page


def test_only_profile_csv_says_what_is_missing():
    p, report, w = linkedin.import_export([("Profile.csv", FILES["Profile.csv"].encode())], None)
    assert p.name == "Alex Morgan" and p.summary == "I build payment systems." and not p.experience
    assert report[-1].startswith("Not in this export: Positions, Education, Skills, Job seeker preferences.")
    assert not (w.titles or w.locations or w.remote)


def test_other_files_are_refused():
    with pytest.raises(linkedin.LinkedInError, match="isn't part of a LinkedIn export"):
        linkedin.read_files([("notes.txt", b"hello")])
