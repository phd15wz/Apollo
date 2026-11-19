"""Generic recovery record for one caller-named attempt.

The file stores the phase, a policy fingerprint, a baseline, and the client
order ids that were submitted. The caller supplies the plan id. This module
does not choose a portfolio, store policy numbers, or store API keys.
A different plan id or fingerprint refuses to load. Whether the current
process may still open orders is not written here.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1
PHASES = frozenset({"unarmed", "armed", "entering", "monitoring", "unwinding", "flat", "escalated"})
_PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{16,64}$")


class AuditError(Exception):
    """The recovery file is missing, conflicting, or not this plan."""


@dataclass
class AuditOrder:
    cl_ord_id: str
    inst_id: str
    intent: str
    submitted: bool
    waits: int = 0


@dataclass
class RecoveryAudit:
    plan_id: str
    phase: str
    policy_fingerprint: str
    baseline_ms: int | None = None
    last_step_ms: int | None = None
    close_attempts: int = 0
    entries_sealed: bool = False
    orders: list[AuditOrder] = field(default_factory=list)
    escalation_reason: str = ""

    def order_by_client_id(self, cl_ord_id: str) -> AuditOrder | None:
        for row in self.orders:
            if row.cl_ord_id == cl_ord_id:
                return row
        return None

    def to_json(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "plan_id": self.plan_id,
            "phase": self.phase,
            "policy_fingerprint": self.policy_fingerprint,
            "baseline_ms": self.baseline_ms,
            "last_step_ms": self.last_step_ms,
            "close_attempts": self.close_attempts,
            "entries_sealed": self.entries_sealed,
            "escalation_reason": self.escalation_reason,
            "orders": [
                {
                    "cl_ord_id": row.cl_ord_id,
                    "inst_id": row.inst_id,
                    "intent": row.intent,
                    "submitted": row.submitted,
                    "waits": row.waits,
                }
                for row in self.orders
            ],
        }


def create_audit(path: Path, *, plan_id: str, policy_fingerprint: str) -> RecoveryAudit:
    _check_identity(plan_id, policy_fingerprint)
    if path.exists():
        raise AuditError(f"audit file already exists: {path}")
    audit = RecoveryAudit(plan_id=plan_id, phase="unarmed", policy_fingerprint=policy_fingerprint)
    save_audit(path, audit)
    return audit


def save_audit(path: Path, audit: RecoveryAudit) -> None:
    _check_identity(audit.plan_id, audit.policy_fingerprint)
    if audit.phase not in PHASES:
        raise AuditError("refusing to write an audit record with an unknown phase")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(audit.to_json(), sort_keys=True, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def load_audit(path: Path, *, plan_id: str, policy_fingerprint: str) -> RecoveryAudit:
    _check_identity(plan_id, policy_fingerprint)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError("audit file cannot be read") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or raw.get("plan_id") != plan_id:
        raise AuditError("audit file is not this plan")
    if raw.get("policy_fingerprint") != policy_fingerprint:
        raise AuditError("audit file was written under a different policy fingerprint")
    phase = raw.get("phase")
    if phase not in PHASES:
        raise AuditError("audit phase is not recognized")
    orders: list[AuditOrder] = []
    for row in raw.get("orders") or []:
        if not isinstance(row, dict):
            raise AuditError("audit order row is not an object")
        orders.append(
            AuditOrder(
                cl_ord_id=str(row.get("cl_ord_id") or ""),
                inst_id=str(row.get("inst_id") or ""),
                intent=str(row.get("intent") or ""),
                submitted=row.get("submitted") is True,
                waits=_nonneg_int(row.get("waits")),
            )
        )
    return RecoveryAudit(
        plan_id=plan_id,
        phase=str(phase),
        policy_fingerprint=policy_fingerprint,
        baseline_ms=_opt_int(raw.get("baseline_ms")),
        last_step_ms=_opt_int(raw.get("last_step_ms")),
        close_attempts=int(raw.get("close_attempts") or 0),
        entries_sealed=raw.get("entries_sealed") is True,
        orders=orders,
        escalation_reason=str(raw.get("escalation_reason") or ""),
    )


def _check_identity(plan_id: str, policy_fingerprint: str) -> None:
    if not _PLAN_ID.fullmatch(plan_id):
        raise AuditError("plan id must be a short lowercase label supplied by the caller")
    if not _FINGERPRINT.fullmatch(policy_fingerprint):
        raise AuditError("policy fingerprint must be a hex digest supplied by the caller")


def _nonneg_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AuditError("audit wait count is not a non-negative integer")
    return value


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuditError("audit timestamp is not an integer")
    return value
