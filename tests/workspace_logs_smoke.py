"""Exercise workspace logs and conversation discovery using synthetic accounts."""
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import password_hash
from app.auth_models import User
from app.db import RunEventRecord, SessionLocal
from app.main import app
from app.rbac import seed_roles
from model_network_smoke import ORIGIN, body, model, post


def get(client, category="all", reference=""):
    return body(client.get("/api/v1/workspace-logs",
                           params={"category": category, "reference": reference}), 200)["items"]


def check():
    assert os.environ.get("FINANCE_DATA_DIR")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as owner:
        initial = body(post(owner, "/auth/setup", {"username": "audit_owner", "password": "AuditOwner!784"}), 201)
        with closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53001))) as anonymous:
            assert anonymous.get("/api/v1/workspace-logs").status_code == 401
            failure = body(post(anonymous, "/auth/login", {"username": "audit_owner", "password": "PrivateInput_42"}), 401)
        reference = failure["request_id"]
        login = get(owner, "login", reference)
        assert len(login) == 1 and login[0]["code"] == "INVALID_CREDENTIALS"
        assert get(owner, "error", reference) == login
        assert "PrivateInput_42" not in json.dumps(get(owner))
        browser = body(post(owner, "/workspace-logs/client-error", {"code": "CONVERSATION_LIST_TIMEOUT"}), 201)
        again = body(post(owner, "/workspace-logs/client-error", {"code": "CONVERSATION_LIST_TIMEOUT"}), 201)
        assert browser == again and len(get(owner, "error", browser["request_id"])) == 1
        assert post(owner, "/workspace-logs/client-error", {"code": "RAW_PRIVATE_PAYLOAD"}).status_code == 422
        body(post(owner, "/roles", {"name": "Log test role", "base_role": "member", "permissions": []}), 201)
        assert any(item["action"].startswith("role_create") for item in get(owner, "operation"))
        assert any(item["action"] == "POST /api/v1/roles" for item in get(owner, "operation"))
        assert body(owner.get("/api/v1/agents/conversations"), 200) == {"items": []}
        fixture = model(owner, 53951)
        agent = body(post(owner, "/agents", {"name": "Synthetic assistant", "agent_type": "simple",
            "planner_mode": "model", "model_config_id": fixture["id"]}), 201)
        body(post(owner, f"/agents/{agent['id']}/publish", {}), 201)
        with patch("app.agents.service._references", side_effect=AssertionError("History scanned")):
            choices = body(owner.get("/api/v1/agents/conversations"), 200)["items"]
        assert len(choices) == 1 and choices[0]["id"] == agent["id"]

        user = initial["user"]
        with SessionLocal.begin() as session:
            for username, workspace, role in (("audit_member", user["workspace_id"], "member"),
                                               ("audit_other", "other-workspace", "super_admin")):
                seed_roles(session, user["tenant_id"], workspace)
                session.add(User(id=username, username=username, display_name=username, password_hash=password_hash("AuditOther!789"),
                    tenant_id=user["tenant_id"], workspace_id=workspace, role=role, active=True, allowed_entities="[]",
                    created_at=datetime.now(timezone.utc).isoformat(), must_change_password=False))
            session.add(RunEventRecord(tenant_id=user["tenant_id"], workspace_id=user["workspace_id"],
                run_id="run_fixture", sequence=1, payload=json.dumps({
                    "type": "effect.unknown", "created_at": datetime.now(timezone.utc).isoformat(),
                    "data": {"error": {"code": "OUTCOME_UNKNOWN", "message": "Private tool response"}},
                })))
        assert get(owner, "error", "run_fixture")[0]["code"] == "OUTCOME_UNKNOWN"
        assert "Private tool response" not in json.dumps(get(owner))
        for username in ("audit_member", "audit_other"):
            with closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53002))) as caller:
                body(post(caller, "/auth/login", {"username": username, "password": "AuditOther!789"}), 200)
                if username == "audit_member":
                    assert caller.get("/api/v1/workspace-logs").status_code == 403
                else:
                    assert get(caller, "all", reference) == [] and get(caller, "error", "run_fixture") == []
    print("PASS scoped logs, login and error filters, search, redaction, browser deduplication and conversation discovery")


if __name__ == "__main__":
    check()
