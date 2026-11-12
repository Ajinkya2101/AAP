"""Determinism check (aap.eval.determinism, PLAN-MID-DECEMBER E1): which fields differ between answers,
and the report's verdict."""

import json

from aap.eval.determinism import FileResult, differing_fields, report


def test_differing_fields_names_paths_and_values():
    a = {"total": "10.00", "lines": [{"amount": "5.00"}, {"amount": "5.00"}]}
    b = {"total": "10.00", "lines": [{"amount": "5.00"}, {"amount": "5.01"}]}
    c = {"total": "10.00", "lines": [{"amount": "5.00"}]}
    assert differing_fields([a, a]) == {}
    assert differing_fields([a, b]) == {"lines.1.amount": ["5.00", "5.01"]}
    assert differing_fields([a, c])["lines.count"] == [2, 1]


def test_report_passes_only_when_both_modes_agree():
    same = json.dumps({"total": "1"})
    other = json.dumps({"total": "2"})
    stable = FileResult("a.pdf", [same, same], [same, same])
    flips = FileResult("b.pdf", [same, same], [same, other])
    text, ok = report([stable])
    assert ok and "1 of 1 files" in text
    text, ok = report([stable, flips])
    assert not ok and "1 of 2 files" in text and "total" in text
    _, ok = report([FileResult("c.pdf", [same], [same], errors=["concurrent 0: timeout"])])
    assert not ok
