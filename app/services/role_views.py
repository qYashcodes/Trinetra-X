from __future__ import annotations


ROLE_VIEW_REGISTRY: dict[str, dict] = {
    "login": {"roles": ["public"], "panels": {}},
    "case-intake": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "live-intake": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "docket": {
        "roles": ["io", "supervisor", "admin"],
        "panels": {
            "docket-summary": ["io", "supervisor", "admin"],
            "working-trail": ["io", "admin"],
            "acp-dispatch-status": ["supervisor", "admin"],
            "case-table": ["io", "supervisor", "admin"],
        },
    },
    "trace": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "investigator-canvas": {
        "roles": ["io", "supervisor", "admin"],
        "panels": {
            "trace-controls": ["io", "admin"],
            "evidence-drawer": ["io", "supervisor", "admin"],
        },
    },
    "finding": {
        "roles": ["io", "supervisor", "admin"],
        "panels": {
            "pre-notice-review": ["io", "admin"],
            "shared-finding-review": ["supervisor", "admin"],
        },
    },
    "notice": {
        "roles": ["io", "supervisor", "admin"],
        "panels": {
            "notice-authoring": ["io", "admin"],
            "countersignature": ["supervisor", "admin"],
        },
    },
    "dispatch-tracker": {
        "roles": ["io", "supervisor", "admin"],
        "panels": {"dispatch-oversight": ["supervisor", "admin"]},
    },
    "risk": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "integrations": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "audit-log": {"roles": ["io", "supervisor", "admin"], "panels": {}},
    "sessions": {"roles": ["io", "supervisor", "admin"], "panels": {}},
}


def role_view_registry() -> dict[str, dict]:
    return {
        view: {
            "roles": list(spec["roles"]),
            "panels": {name: list(roles) for name, roles in spec.get("panels", {}).items()},
        }
        for view, spec in ROLE_VIEW_REGISTRY.items()
    }
