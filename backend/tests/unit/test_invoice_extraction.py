"""Unit — cleaning the model's answer and matching it with the household's providers."""

import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app import invoice_extraction as ie

HYDRO, BELL = uuid.UUID(int=1), uuid.UUID(int=2)
ELEC, NET, MOBILE = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
PROVIDERS = [(HYDRO, "Hydro-Québec"), (BELL, "Bell")]
SERVICES = [
    (ELEC, HYDRO, "Électricité", "electricity"),
    (NET, BELL, "Internet", "internet"),
    (MOBILE, BELL, "Mobile", "phone"),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (12.5, D("12.5")),
        ("1 240,50 $", D("1240.50")),
        ("1,240.50", D("1240.50")),
        ("114.98", D("114.98")),
        ("", None),
        ("n/a", None),
        (None, None),
        (True, None),
        (float("nan"), None),
        ([1], None),
    ],
)
def test_money_is_read_leniently_and_garbage_becomes_none(raw, expected):
    assert ie._money(raw) == expected


def test_dates_must_be_iso_and_anything_else_is_dropped():
    assert ie._date("2026-10-01") == date(2026, 10, 1)
    assert ie._date("2026-10-01T00:00:00") == date(2026, 10, 1)
    assert ie._date("1er octobre 2026") is None
    assert ie._date("2026-13-40") is None
    assert ie._date(20261001) is None


def test_clean_keeps_good_fields_and_drops_bad_ones():
    cleaned = ie.clean(
        {
            "provider": "  Bell  ",
            "service": "Internet",
            "total": "114,98 $",
            "due_on": "pas une date",
            "issued_on": "2026-10-01",
            "invoice_number": "A-1",
            "consumption_qty": "1 240",
            "consumption_unit": "kWh",
            "taxes": [
                {"name": "TPS", "amount": "5.00"},
                {"name": "bad", "amount": "x"},
                "not a dict",
                {"amount": 9.98},
            ],
        }
    )

    assert cleaned["provider"] == "Bell"
    assert cleaned["total"] == D("114.98")
    assert cleaned["due_on"] is None
    assert cleaned["issued_on"] == date(2026, 10, 1)
    assert cleaned["consumption_qty"] == D("1240")
    assert cleaned["taxes"] == [
        {"name": "TPS", "amount": D("5.00")},
        {"name": "Taxe", "amount": D("9.98")},
    ]


def test_clean_survives_a_non_dict_taxes_and_missing_keys():
    assert ie.clean({"taxes": "none"})["taxes"] == []
    assert ie.clean({})["total"] is None


def test_normalize_drops_accents_case_and_punctuation():
    assert ie.normalize("  Hydro-Québec  ") == "hydro quebec"


def test_build_matches_provider_and_service_by_name():
    fields, extraction = ie.build(
        {"provider": "BELL CANADA", "service": "internet", "total": 100, "due_on": "2026-11-01"},
        PROVIDERS,
        SERVICES,
    )

    assert fields == {"total": D("100"), "due_on": date(2026, 11, 1)}
    candidates = extraction["candidates"]
    assert candidates["provider"] == {"id": str(BELL), "name": "Bell"}
    assert candidates["service"] == {"id": str(NET), "name": "Internet"}
    assert (candidates["new_provider"], candidates["new_service"]) == (None, None)


def test_an_unknown_provider_is_proposed_as_new():
    _, extraction = ie.build({"provider": "Vidéotron", "service": "Internet"}, PROVIDERS, SERVICES)

    candidates = extraction["candidates"]
    assert candidates["provider"] is None and candidates["service"] is None
    assert (candidates["new_provider"], candidates["new_service"]) == ("Vidéotron", "Internet")


def test_a_lone_service_is_proposed_when_the_model_names_none():
    _, extraction = ie.build({"provider": "Hydro-Québec"}, PROVIDERS, SERVICES)

    assert extraction["candidates"]["service"]["id"] == str(ELEC)


def test_an_ambiguous_service_is_left_for_the_person_to_pick():
    _, extraction = ie.build({"provider": "Bell"}, PROVIDERS, SERVICES)

    assert extraction["candidates"]["service"] is None


def test_a_preselected_provider_limits_the_matching():
    _, extraction = ie.build(
        {"provider": "Hydro-Québec", "service": "Internet"},
        PROVIDERS,
        SERVICES,
        preselect_provider_id=BELL,
    )

    assert extraction["candidates"]["provider"]["id"] == str(BELL)
    assert extraction["candidates"]["service"]["id"] == str(NET)
    assert extraction["provider_id"] == str(BELL)


def test_a_preselected_service_wins_over_the_model():
    _, extraction = ie.build(
        {"provider": "Hydro-Québec", "service": "Électricité"},
        PROVIDERS,
        SERVICES,
        preselect_service_id=MOBILE,
    )

    candidates = extraction["candidates"]
    assert candidates["provider"]["id"] == str(BELL)
    assert candidates["service"]["id"] == str(MOBILE)


def test_the_raw_part_is_json_safe():
    import json

    _, extraction = ie.build(
        {"total": "10", "due_on": "2026-11-01", "taxes": [{"name": "TPS", "amount": "1"}]},
        PROVIDERS,
        SERVICES,
    )

    json.dumps(extraction)
    assert extraction["raw"]["total"] == "10"
    assert extraction["raw"]["taxes"] == [{"name": "TPS", "amount": "1"}]
