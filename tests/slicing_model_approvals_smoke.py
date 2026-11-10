"""Verify approval boundaries and the custom slicing workflow with synthetic data."""
from contextlib import closing
import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.auth import password_hash
from app.auth_models import User
from app.db import SessionLocal, utc_now
from app.main import app
from app.rbac import seed_roles
from model_network_smoke import ORIGIN, body, post


def request(client, method, path, value=None, status=200):
    csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
    return body(client.request(method, "/api/v1" + path, json=value,
                               headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}), status)


def login(client, name):
    body(post(client, "/auth/login", {"username": name, "password": "ApprovalFixture!729"}), 200)


def approve(client, path, item, decision="approve", status=200):
    return request(client, "POST", f"{path}/{item['id']}/review",
                   {"expected_revision": item["revision"], "decision": decision}, status)


def catalog(client):
    return {model["id"]: model["config_version"] for model in
            request(client, "GET", "/model-catalog")["items"]}


def check_models(owner, member, other):
    platform = {"name": "Synthetic API", "auth_type": "api_key",
                "api_key": "synthetic_approval_secret", "base_url": "https://example.invalid/v1"}
    provider_path, model_path = "/model-providers", "/models"
    request(owner, "POST", provider_path, {**platform, "visibility": "private"}, 422)
    provider = request(member, "POST", provider_path, {**platform, "visibility": "private"}, 201)
    child = request(member, "POST", f"{provider_path}/{provider['id']}/models",
                    {"name": "Private fixture", "model_name": "fixture"}, 201)
    assert provider["review_status"] == child["review_status"] == "pending"
    assert child["id"] not in catalog(member)
    request(member, "POST", f"{provider_path}/{provider['id']}/test-connection", {}, 409)
    request(member, "POST", f"{model_path}/{child['id']}/test-prompt",
            {"text": "Synthetic", "max_output_tokens": 16}, 409)
    request(other, "GET", f"{provider_path}/{provider['id']}", status=404)
    approve(member, model_path, child, status=403)
    queue = request(owner, "GET", "/model-catalog/reviews")["items"]
    assert len(queue) == 2 and "synthetic_approval_secret" not in str(queue)
    approve(owner, model_path, child, status=409)
    approve(owner, provider_path, provider)
    approve(owner, model_path, child)
    assert child["id"] in catalog(member) and child["id"] not in catalog(other)
    request(owner, "GET", f"{model_path}/{child['id']}", status=404)

    def get(path, item):
        return request(member, "GET", f"{path}/{item['id']}")

    def convert(path, item, scope, status=200):
        current = get(path, item)
        return request(member, "PATCH", f"{path}/{item['id']}",
                       {"visibility": scope, "expected_revision": current["revision"]}, status)

    convert(model_path, child, "public", 409)
    pending = convert(provider_path, provider, "public")
    assert pending["visibility"] == "private" and pending["requested_visibility"] == "public"
    approve(owner, provider_path, pending)
    pending = convert(model_path, child, "public")
    assert child["id"] in catalog(member) and child["id"] not in catalog(other)
    approve(owner, model_path, pending)
    public_version = catalog(other)[child["id"]]
    convert(provider_path, provider, "private", 409)
    pending = convert(model_path, child, "private")
    assert catalog(other)[child["id"]] == public_version
    approve(owner, model_path, pending, "reject")
    assert catalog(other)[child["id"]] == public_version
    pending = convert(model_path, child, "private")
    approve(owner, model_path, pending)
    assert child["id"] not in catalog(other) and catalog(member)[child["id"]] == public_version
    request(other, "GET", f"{model_path}/{child['id']}", status=404)
    pending = convert(provider_path, provider, "private")
    approve(owner, provider_path, pending)
    request(other, "GET", f"{provider_path}/{provider['id']}", status=404)
    return request(owner, "POST", provider_path, {**platform, "auth_type": "none", "api_key": None}, 201)


def check_slicing(owner, member, provider):
    model = request(owner, "POST", f"/model-providers/{provider['id']}/models",
                    {"name": "Synthetic slicing model", "model_name": "fixture",
                     "context_window": 120000, "max_output_tokens": 8000,
                     "capabilities": {"json_schema": True}}, 201)
    route = "/builtin-agents"
    create = {"name": "Synthetic documents", "strategy": "builtin_document_chunker", "formats": ["text"]}
    request(member, "POST", route, create, 403)
    request(owner, "POST", route, {**create, "formats": ["pptx"]}, 422)
    agent = request(owner, "POST", route, create, 201)
    assert not agent["builtin"] and agent["config"]["source_scope"] is None
    path = f"{route}/{agent['id']}"
    prompts = agent["config"]["stage_prompts"]
    prompts["slice"]["system_prompt"] += "\nSLICE_WORKFLOW_EDIT_PROBE"
    settings = {"expected_revision": agent["config"]["revision"], "model_config_id": model["id"],
                "stage_prompts": prompts, "recipe_options": {**agent["config"]["recipe_options"], "max_repair_rounds": 0}}
    preview = request(owner, "POST", path + "/workflow-preview", settings)
    assert any("SLICE_WORKFLOW_EDIT_PROBE" in stage["effective_system_prompt"] for stage in preview["stages"])
    configured = request(owner, "PUT", path + "/settings", settings)
    assert configured["config"]["recipe_options"]["max_repair_rounds"] == 0

    def upload(kb, filename, content):
        token = body(owner.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        return body(owner.post(f"/api/v1/knowledge-bases/{kb}/documents",
            params={"profile": "portable-slicing-1.0.0"}, files={"file": (filename, content)},
            headers={"Origin": ORIGIN, "X-CSRF-Token": token}), 201)["id"]

    kb_a, kb_b = [request(owner, "POST", "/knowledge-bases", {"name": name}, 201)["id"] for name in ("One", "Two")]
    doc_a, doc_b = [upload(kb, "policy.txt", "审批须保留凭证和主管签字。".encode()) for kb in (kb_a, kb_b)]
    table = upload(kb_a, "table.csv", b"item,amount\ncost,10\n")
    for doc in (doc_a, doc_b):
        choices = request(owner, "GET", f"{route}/document-agent-choices?document_id={doc}")["items"]
        assert choices == [{"id": agent["id"], "name": agent["name"]}]
        with patch("app.execution.model_network.prepare_model_address", side_effect=AssertionError("Model call during preview")):
            budget = request(owner, "POST", path + "/document-job-budget",
                             {"document_id": doc, "expected_revision": configured["config"]["revision"]})
        assert budget["mode"] == "preview_only"
    assert request(owner, "GET", f"{route}/document-agent-choices?document_id={table}")["items"] == []
    request(owner, "GET", f"{path}/document-job-options?document_id={table}", status=422)
    request(owner, "PUT", path + "/settings",
            {"expected_revision": configured["config"]["revision"], "source_scope": [kb_a]})
    request(owner, "GET", f"{path}/document-job-options?document_id={doc_b}", status=422)
    assert request(owner, "GET", f"{path}/document-job-options?document_id={doc_a}")["execution_ready"]


def check():
    assert os.environ.get("FINANCE_DATA_DIR")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as owner:
        user = body(post(owner, "/auth/setup", {"username": "approval_owner", "password": "ApprovalFixture!729"}), 201)["user"]
        with SessionLocal.begin() as session:
            seed_roles(session, user["tenant_id"], user["workspace_id"])
            for name, role in (("approval_member", "member"), ("approval_admin", "admin")):
                session.add(User(id=name, username=name, display_name=name, password_hash=password_hash("ApprovalFixture!729"),
                                 tenant_id=user["tenant_id"], workspace_id=user["workspace_id"], role=role, active=True,
                                 allowed_entities="[]", created_at=utc_now(), must_change_password=False))
        with closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53001))) as member, \
             closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53002))) as other:
            login(member, "approval_member")
            login(other, "approval_admin")
            check_slicing(owner, member, check_models(owner, member, other))
    print("PASS approvals, visibility transitions, private isolation, editable workflow, file matching and default scope")


if __name__ == "__main__":
    check()
