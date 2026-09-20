from __future__ import annotations

from typing import Any, Mapping, MutableMapping


VALID_MODES = {"fixture", "live"}
VALID_ROLES = {"io", "supervisor", "admin"}
VALID_STRATEGIES = {"dominant_fund_flow", "value_weighted"}
CONTEXT_KEYS = {
    "role",
    "mode",
    "case_id",
    "snapshot_id",
    "finding_id",
    "notice_id",
    "dispatch_id",
    "strategy",
    "focus",
}


def empty_working_context(*, role: str, mode: str = "fixture") -> dict[str, Any]:
    return {
        "role": role if role in VALID_ROLES else "io",
        "mode": mode if mode in VALID_MODES else "fixture",
        "case_id": None,
        "snapshot_id": None,
        "finding_id": None,
        "notice_id": None,
        "dispatch_id": None,
        "strategy": None,
        "focus": None,
    }


def read_working_context(
    session_state: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    raw = session_state.get("working_context") or session_state.get("active_case") or {}
    context = empty_working_context(role=role)
    for key in CONTEXT_KEYS:
        if key in raw:
            context[key] = raw[key]
    context["role"] = role if role in VALID_ROLES else "io"
    context["mode"] = context["mode"] if context["mode"] in VALID_MODES else "fixture"
    strategy = context.get("strategy")
    context["strategy"] = strategy if strategy in VALID_STRATEGIES else None
    for key in ("case_id", "snapshot_id", "finding_id", "notice_id", "dispatch_id"):
        value = context.get(key)
        context[key] = int(value) if isinstance(value, (int, str)) and str(value).isdigit() else None
    focus = context.get("focus")
    context["focus"] = str(focus)[:200] if focus not in (None, "") else None
    return context


def write_working_context(
    session_state: MutableMapping[str, Any],
    *,
    role: str,
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    context = read_working_context(session_state, role=role)
    for key, value in patch.items():
        if key in CONTEXT_KEYS and key != "role":
            context[key] = value
    context = read_working_context({"working_context": context}, role=role)
    session_state["working_context"] = context
    # Keep the legacy key during the additive transition.
    session_state["active_case"] = {
        "id": context["case_id"],
        "ack_no": patch.get("ack_no") or (session_state.get("active_case") or {}).get("ack_no"),
        "snapshot_id": context["snapshot_id"],
        "finding_id": context["finding_id"],
    }
    return context
