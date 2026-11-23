"""Salaries: formatting source fields, finding one in posting text, and converting to € per month."""

import pytest

from cvmaker import salary


@pytest.mark.parametrize("args, expected", [
    ((60000, 75000, "EUR", "year"), "€60k-75k/year"),
    ((60000, 60000, "eur", "yearly"), "€60k/year"),
    ((4500, None, "USD", "month"), "$4,500/month"),
    ((None, 9000, "GBP", ""), "£9,000"),
    ((5000, 7000, "PLN", "monthly"), "5,000-7,000 PLN/month"),
    ((45, 60, "USD", "hr"), "$45-60/hour"),
    ((None, None, "EUR", "year"), ""),
    ((0, 0, "EUR", "year"), ""),
])
def test_format_salary(args, expected):
    assert salary.format_salary(*args) == expected


@pytest.mark.parametrize("text, expected", [
    ("The salary range is €62,000 to €87,200 per year.", "€62,000 to €87,200 per year"),
    ("Заплата: 4 500 - 6 000 лв. бруто", "4 500 - 6 000 лв. бруто"),
    ("Compensation: $120k-$150k", "$120k-$150k"),
    ("Base pay 85000 EUR gross per year", "85000 EUR gross per year"),
    ("We pay £45/hour for contractors", "£45/hour"),
    ("€87\n,200 base salary", "€87,200"),
])
def test_find_salary(text, expected):
    assert salary.find_salary(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "We raised $40 million in funding last year.",
    "Our revenue grew to €5M and we serve 2000 customers.",
    "Join 3,000 engineers across 12 offices.",
    "Get a €50 voucher for lunch.",            # an amount, but no salary wording and too small
    "Annual salary budget: €20",               # implausibly small for a salary
])
def test_find_salary_ignores_other_numbers(text):
    assert salary.find_salary(text) == ""


@pytest.mark.parametrize("text, expected", [
    ("€60k-72k/year", 5500),                    # yearly range -> midpoint / 12
    ("€4,000/month", 4000),
    ("4 500 - 6 000 лв. бруто", round(5250 / 1.95583)),  # no period, under 15k: monthly
    ("$120,000", round(120000 / 12 * salary.EUR_RATES["USD"])),  # no period, 15k or more: yearly
    ("£50/hour", round(50 * 168 * salary.EUR_RATES["GBP"])),
    ("€400/day", 400 * 21),
])
def test_monthly_eur(text, expected):
    assert salary.monthly_eur(text) == expected


@pytest.mark.parametrize("text", ["", "competitive", "60k-70k", "$500 per task", "€5/month", "€9,000,000/month"])
def test_monthly_eur_unreadable(text):
    assert salary.monthly_eur(text) is None


def test_format_monthly():
    assert salary.format_monthly(5500) == "≈ €5.5k/mo"
    assert salary.format_monthly(850) == "≈ €850/mo"
    assert salary.format_monthly(None) == ""
