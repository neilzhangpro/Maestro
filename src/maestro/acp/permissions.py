"""Permission helpers for ACP tool approvals."""

from __future__ import annotations


VALID_PERMISSION_POLICIES = {"reject-once", "allow-always", "allow-once"}


def build_permission_result(policy: str) -> dict[str, dict[str, str]]:
    if policy not in VALID_PERMISSION_POLICIES:
        raise ValueError(f"Unsupported ACP permission policy: {policy}")
    return {"outcome": {"outcome": "selected", "optionId": policy}}
