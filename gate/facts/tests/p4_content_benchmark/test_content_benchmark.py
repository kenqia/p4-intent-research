import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
SURFACES = ("研究结论", "标题", "摘要", "正文", "图注", "最终建议")


def read_rows(path, id_field):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    by_id = {row[id_field]: row for row in rows}
    assert len(rows) == len(by_id)
    return by_id


@pytest.fixture(scope="module")
def market():
    return read_rows(HERE / "author/market.csv", "evidence_id")


@pytest.fixture(scope="module")
def full_market(market):
    supplement = read_rows(HERE / "author/market-full-only.csv", "evidence_id")
    assert market.keys().isdisjoint(supplement)
    return market | supplement


@pytest.fixture(scope="module")
def claims():
    return json.loads((HERE / "review/claims.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "suffix,period_tail,expected",
    [("", "-09", ("16.6", "34.7", "-18.1")), (":ytd", "-01/09", ("100.4", "226.0", "-125.6"))],
    ids=["september", "jan-sep"],
)
def test_a_legal_wholesale_decomposition(full_market, suffix, period_tail, expected):
    values = {}
    for year in (2025, 2026):
        for series in ("wholesale", "export", "retail"):
            row = full_market[f"cpca:{year}-09:{series}{suffix}"]
            assert (row["period"], row["unit"], row["series"]) == (f"{year}{period_tail}", "万辆", series)
            values[year, series] = Decimal(row["value"])
        assert 0 <= values[year, "export"] <= values[year, "wholesale"]

    wholesale_change = values[2026, "wholesale"] - values[2025, "wholesale"]
    export_change = values[2026, "export"] - values[2025, "export"]
    retail_change = values[2026, "retail"] - values[2025, "retail"]
    non_export_change = (values[2026, "wholesale"] - values[2026, "export"]) - (
        values[2025, "wholesale"] - values[2025, "export"]
    )
    assert (wholesale_change, export_change, non_export_change) == tuple(map(Decimal, expected))
    assert wholesale_change == export_change + non_export_change
    assert non_export_change != retail_change


def rejected_surfaces(report_claims):
    rejected = set()
    for claim in report_claims:
        surface, relation = claim["surface"], claim["relation"]
        assert surface in SURFACES
        inputs = set(claim["inputs"])
        if relation == "inventory_flow" and inputs == {"non_export_wholesale", "retail"}:
            rejected.add(surface)
        if relation == "wholesale_partition" and "retail" in inputs:
            rejected.add(surface)
    return rejected


def test_b_historical_claims_persist_after_tool_rejection(claims):
    inventory = claims["inventory"]
    assert rejected_surfaces([inventory["stage05_body"], inventory["stage05_recommendation"]]) == {"正文", "最终建议"}
    assert rejected_surfaces([inventory["upstream_heading"], inventory["upstream_body"]]) == {"标题", "正文"}


@pytest.mark.parametrize("surface", SURFACES)
def test_b_cross_scope_inference_is_rejected_on_every_surface(claims, surface):
    inventory = claims["inventory"]
    valid = inventory["synthetic_parallel_observation"]
    assert not rejected_surfaces([valid | {"surface": field} for field in SURFACES])
    report = [
        (inventory["stage05_body"] if field == surface else valid) | {"surface": field}
        for field in SURFACES
    ]
    assert rejected_surfaces(report) == {surface}


def withdrawal_errors(claim, visible):
    basis = set(claim["basis"])
    visible_basis = basis & visible.keys()
    windows = {"ytd" if "/" in visible[key]["period"] else "month" for key in visible_basis}
    errors = set()
    if basis - visible.keys():
        errors.add("unavailable_basis")
    if windows - {claim["window"]}:
        errors.add("wrong_window")
    if set(claim["claimed_absent"]) & visible.keys():
        errors.add("false_absence")
    return errors


def test_c_withdraw_only_current_cumulative_export(market, full_market, claims):
    withdrawal = claims["withdrawal"]
    assert full_market.keys() - market.keys() == {"cpca:2026-09:export:ytd", "cpca:2026-09:export_yoy:ytd"}
    assert {row["period"] for row in market.values() if row["series"] == "export"} == {
        "2025-09", "2025-01/09", "2026-07", "2026-08", "2026-09"
    }
    assert {row["period"] for row in market.values() if row["series"] == "export_yoy"} == {
        "2025-09", "2026-07", "2026-08", "2026-09"
    }
    assert not withdrawal_errors(withdrawal["september"], market)
    assert not withdrawal_errors(withdrawal["synthetic_reuse"], full_market)
    assert withdrawal_errors(withdrawal["synthetic_reuse"], market) == {"unavailable_basis"}
    assert withdrawal_errors(withdrawal["writeback_heading"], market) == {"wrong_window"}
    assert withdrawal_errors(withdrawal["continued_draft_availability"], market) == {"false_absence"}


def test_d_level_growth_and_implied_comparison_base(claims):
    rows = read_rows(HERE.parents[1] / "data/samples/nbs_nev_production_2026Q3.csv", "metric_id")
    annotations = claims["production"]
    for key, matches in [("direct_quarter", False), ("direct_first_half", False), ("september", True)]:
        claim = annotations[key]
        assert (rows[claim["evidence_id"]]["period_type"] == claim["window"]) is matches

    july, august, september = [rows[key] for key in ["NEV-2026-07-M", *annotations["implicit_basis_inputs"]]]
    assert [(row["stat_period"], row["unit"], row["stat_scope"]) for row in (july, august, september)] == [
        (f"2026-{month}", "万辆", "规模以上工业") for month in ("07", "08", "09")
    ]
    levels = [Decimal(row["value"]) for row in (july, august, september)]
    growth = [Decimal(row["yoy_pct"]) for row in (july, august, september)]
    assert levels[0] < levels[1] < levels[2] and growth[0] > growth[1] > growth[2] > 0
    assert (levels[1] - levels[0], levels[2] - levels[1]) == (Decimal("9.7"), Decimal("22.3"))
    implied_august = levels[1] / (1 + growth[1] / 100)
    implied_september = levels[2] / (1 + growth[2] / 100)
    assert round((levels[2] / levels[1] - 1) * 100, 1) == Decimal("13.5")
    assert round((implied_september / implied_august - 1) * 100, 1) == Decimal("18.4")

    monthly_sources = {rows[key]["source_id"] for key in annotations["implicit_basis_inputs"]}
    cumulative_sources = {rows[key]["source_id"] for key in annotations["two_stage_cross_check_inputs"]}
    assert monthly_sources == cumulative_sources == {"NBS-2026-08", "NBS-2026-09"}
