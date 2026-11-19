"""Recovery record for the 2026-11-19 book.

The file format is ``tools.okx.recovery``. This module only binds that
record to this plan id. It does not store the policy numbers or API keys.
"""

from __future__ import annotations

from pathlib import Path

from tools.okx.pilot_policy import PLAN_ID
from tools.okx.recovery import AuditError, AuditOrder, RecoveryAudit as PilotAudit, create_audit as _create
from tools.okx.recovery import load_audit as _load
from tools.okx.recovery import save_audit

__all__ = [
    "AuditError",
    "AuditOrder",
    "PilotAudit",
    "PLAN_ID",
    "create_audit",
    "load_audit",
    "save_audit",
]


def create_audit(path: Path, *, policy_fingerprint: str) -> PilotAudit:
    return _create(path, plan_id=PLAN_ID, policy_fingerprint=policy_fingerprint)


def load_audit(path: Path, *, policy_fingerprint: str) -> PilotAudit:
    return _load(path, plan_id=PLAN_ID, policy_fingerprint=policy_fingerprint)
