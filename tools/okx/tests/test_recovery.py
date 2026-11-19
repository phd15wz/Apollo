"""Offline recovery-record checks. No portfolio and no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.okx.recovery import AuditError, AuditOrder, create_audit, load_audit, save_audit

FINGERPRINT = "ab" * 8


def test_audit_round_trip_keeps_submitted_ids_and_refuses_a_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    audit = create_audit(path, plan_id="fixture-book", policy_fingerprint=FINGERPRINT)
    audit.phase = "entering"
    audit.baseline_ms = 10
    audit.orders.append(AuditOrder("ord1", "ABC-USDT-SWAP", "entry", True, waits=1))
    save_audit(path, audit)
    text = path.read_text(encoding="utf-8")
    assert "test-key" not in text
    assert "fixture-book" in text
    loaded = load_audit(path, plan_id="fixture-book", policy_fingerprint=FINGERPRINT)
    assert loaded.orders[0].waits == 1
    assert loaded.orders[0].submitted is True
    assert loaded.baseline_ms == 10
    with pytest.raises(AuditError):
        create_audit(path, plan_id="fixture-book", policy_fingerprint=FINGERPRINT)
    with pytest.raises(AuditError, match="fingerprint"):
        load_audit(path, plan_id="fixture-book", policy_fingerprint="cd" * 8)
    with pytest.raises(AuditError, match="plan"):
        load_audit(path, plan_id="other-book", policy_fingerprint=FINGERPRINT)


def test_audit_identity_is_required() -> None:
    with pytest.raises(AuditError):
        create_audit(Path("/tmp/does-not-write.json"), plan_id="", policy_fingerprint=FINGERPRINT)
    with pytest.raises(AuditError):
        create_audit(Path("/tmp/does-not-write.json"), plan_id="fixture-book", policy_fingerprint="not-hex")


def test_permanent_tree_has_no_portfolio_files() -> None:
    root = Path("tools/okx")
    for name in ("pilot_policy.py", "pilot_control.py", "pilot_book.py", "pilot_audit.py"):
        assert not (root / name).exists()
    assert not Path("docs/pilot-risk-plan-runbook.md").exists()
