"""Unit — the pure cost rules: paid state, alert reference, flags, monthly totals."""

import uuid
from datetime import date
from decimal import Decimal as D

from app import costs

TODAY = date(2026, 10, 10)


def test_an_invoice_with_a_payment_date_is_paid():
    assert costs.is_paid(date(2026, 10, 1), False, date(2026, 11, 1), TODAY) is True


def test_an_auto_pay_invoice_is_paid_from_its_due_date():
    assert costs.is_paid(None, True, date(2026, 10, 10), TODAY) is True
    assert costs.is_paid(None, True, date(2026, 10, 11), TODAY) is False


def test_a_manual_invoice_is_unpaid_until_marked():
    assert costs.is_paid(None, False, date(2026, 1, 1), TODAY) is False


def test_an_invoice_with_no_due_date_is_never_auto_paid():
    assert costs.is_paid(None, True, None, TODAY) is False


def test_with_fewer_than_three_invoices_the_reference_is_the_expected_cost():
    assert costs.reference([D("100"), D("120")], D("90")) == D("90")
    assert costs.reference([], None) is None


def test_with_three_invoices_the_reference_is_their_mean():
    assert costs.reference([D("100"), D("110"), D("120")], D("90")) == D("110")


def test_only_the_last_six_invoices_count():
    history = [D("1000")] + [D("100")] * 6

    assert costs.reference(history, None) == D("100")


def test_a_bill_exactly_at_the_threshold_is_not_flagged():
    assert costs.is_flagged(D("120.00"), D("100"), 20) is False
    assert costs.is_flagged(D("120.01"), D("100"), 20) is True


def test_nothing_is_flagged_without_a_reference():
    assert costs.is_flagged(D("999"), None, 20) is False


def test_series_compares_each_invoice_with_the_ones_before_it():
    ids = [uuid.UUID(int=i) for i in range(1, 6)]
    rows = [
        (ids[3], date(2026, 4, 1), D("100")),
        (ids[0], date(2026, 1, 1), D("100")),
        (ids[2], date(2026, 3, 1), D("100")),
        (ids[4], date(2026, 5, 1), D("200")),
        (ids[1], date(2026, 2, 1), D("100")),
    ]

    points = costs.series(rows, expected=D("50"), threshold_pct=20)

    assert [p.invoice_id for p in points] == ids
    # The first three are compared with the expected cost (50), the next with their mean.
    assert [p.flagged for p in points] == [True, True, True, False, True]
    assert points[3].reference == D("100.00")


def test_monthly_totals_group_by_month_and_sort():
    rows = [
        (date(2026, 9, 28), D("10.50")),
        (date(2026, 9, 1), D("20")),
        (date(2026, 8, 15), D("5")),
    ]

    assert costs.monthly_totals(rows) == [("2026-08", D("5")), ("2026-09", D("30.50"))]
