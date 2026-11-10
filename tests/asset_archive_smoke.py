"""Archive/restore with live references and readable completed history."""
from datetime import timedelta
import json
import os
import time
from sqlalchemy import select

from fastapi.testclient import TestClient

from app.main import app
from app.auth import now
from app.db import Resource, SessionLocal, new_id
from model_network_smoke import ORIGIN, body, post


def workflow(name):
    return {"name": name, "schemaVersion": "2.0", "mode": "demo",
            "nodes": [{"id": "start", "type": "start"}, {"id": "output", "type": "output", "inputs": {"text": "fixture result"}}],
            "edges": [{"source": "start", "target": "output"}]}


def check():
    assert os.environ.get("FINANCE_DATA_DIR"), "Use a new isolated data directory"
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as client:
        assert body(client.get("/api/v1/auth/status"), 200)["initialized"] is False
        body(post(client, "/auth/setup", {"username": "test_admin", "password": "Synthetic!archive-2026"}), 201)

        def change(kind, identifier, operation):
            return post(client, f"/agents/recycle-bin/{kind}/{identifier}/{operation}", {})

        saved = body(post(client, "/workflows", workflow("Fixture history")), 201)
        identifier = saved["id"]
        run = body(post(client, "/runs", {"workflow_id": identifier, "prompt": "Synthetic fixture"}), 202)
        for _ in range(120):
            run = body(client.get("/api/v1/runs/" + run["id"]), 200)
            if run["status"] not in ("queued", "running"):
                break
            time.sleep(.05)
        assert run["status"] == "succeeded", run.get("error")

        with SessionLocal() as database:
            stored = database.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run["id"]))
            payload = json.loads(stored.payload)
            original_payload = stored.payload
            payload["status"] = "outcome_unknown"
            stored.payload = json.dumps(payload)
            database.commit()
        assert body(change("workflow", identifier, "archive"), 409)["error"]["code"] == "ASSET_IN_USE"
        with SessionLocal() as database:
            stored = database.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run["id"]))
            stored.payload = original_payload
            database.commit()

        item = body(change("workflow", identifier, "archive"), 200)
        assert item["id"] == identifier
        assert client.get(f"/api/v1/workflows/{identifier}").status_code == 404
        assert identifier not in {item["id"] for item in body(client.get("/api/v1/workflows"), 200)["items"]}
        assert post(client, "/runs", {"workflow_id": identifier, "prompt": "Fixture"}).status_code == 404
        assert body(client.get("/api/v1/runs/" + run["id"]), 200)["outputs"] == run["outputs"]
        assert client.get("/api/v1/runs/" + run["id"] + "/trace").status_code == 200
        assert identifier in {item["id"] for item in body(client.get("/api/v1/agents/recycle-bin"), 200)["items"]}
        body(change("workflow", identifier, "restore"), 200)
        restored = body(client.get(f"/api/v1/workflows/{identifier}"), 200)
        assert restored["graph_sha256"] == saved["graph_sha256"]

        agent = body(post(client, "/agents", {"name": "Fixture bound agent", "agent_type": "advanced",
            "planner_mode": "demo", "main_workflow_id": identifier}), 201)
        blocked = body(change("workflow", identifier, "archive"), 409)
        assert blocked["error"]["code"] == "ASSET_IN_USE"
        body(change("agent", agent["id"], "archive"), 200)
        assert client.get(f"/api/v1/agents/{agent['id']}").status_code == 404
        body(change("workflow", identifier, "archive"), 200)
        blocked = body(change("agent", agent["id"], "restore"), 409)
        assert blocked["error"]["code"] == "ASSET_DEPENDENCY_ARCHIVED"
        body(change("workflow", identifier, "restore"), 200)
        body(change("agent", agent["id"], "restore"), 200)
        assert body(client.get(f"/api/v1/agents/{agent['id']}"), 200)["main_workflow_id"] == identifier

        release_source = body(post(client, "/workflows", workflow("Fixture release")), 201)
        release_path = f"/platform/assets/workflow/{release_source['id']}/api/publish"
        body(post(client, release_path, {}), 201)
        blocked = body(change("workflow", release_source["id"], "archive"), 409)
        assert blocked["error"]["code"] == "ASSET_IN_USE"
        if os.environ.get("FINANCE_APPLICATION_API_ENABLED") == "true":
            external_source = body(post(client, "/workflows", workflow("Fixture external release")), 201)
            application = body(post(client, "/applications", {"name": "Fixture release", "type": "workflow"}), 201)
            versions = f"/applications/{application['application_id']}/versions"
            source = {"kind": "workflow", "id": external_source["id"]}
            preview = body(post(client, versions+"/preview", {"source": source}), 200)
            body(post(client, versions, {"source": source, "expected_application_revision": application["revision"],
                                         "expected_source_sha256": preview["source_sha256"]}), 201)
            assert body(change("workflow", external_source["id"], "archive"), 409)["error"]["code"] == "ASSET_IN_USE"

        control = "/api/v1/connections/origin-policy"
        current = body(client.get(control), 200)
        assert current["origins"] == ["http://127.0.0.1:8011"]
        def put(path, data):
            csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
            return client.put(path, json=data, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
        connection = {"name": "Fixture connection", "base_url": "http://127.0.0.1:8011"}
        existing = body(post(client, "/connections", connection), 201)
        schema = {"type":"object", "properties":{}, "additionalProperties":False}
        plugin = body(post(client, "/plugins", {"name":"Fixture plugin", "default_connection_id":existing["id"],
            "endpoints":[{"id":"test","name":"Fixture endpoint","path":"/test","input_schema":schema,"output_schema":schema}]}), 201)
        body(put(control, {"origins": [], "expected_revision": current["revision"]}), 200)
        assert body(post(client, "/connections", connection), 403)["error"]["code"] == "CONNECTION_BLOCKED"
        assert body(post(client, f"/plugins/{plugin['id']}/test", {"endpoint_id":"test"}), 403)["error"]["code"] == "CONNECTION_BLOCKED"
        assert put(control, {"origins": ["http://127.0.0.1:8011"], "expected_revision": current["revision"]}).status_code == 409
        assert put(control, {"origins": ["https://outside.example"], "expected_revision": current["revision"]+1}).status_code == 403
        body(put(control, {"origins": ["http://127.0.0.1:8011"], "expected_revision": current["revision"]+1}), 200)
        body(post(client, "/connections", connection), 201)

        private_workflow = body(post(client, "/workflows", workflow("Private recycle fixture")), 201)
        body(change("workflow", private_workflow["id"], "archive"), 200)
        member = body(post(client, "/users", {"username": "test_member", "role_id": "member"}), 201)
        restricted = TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53001))
        try:
            body(post(restricted, "/auth/login", {"username": "test_member", "password": member["initial_password"]}), 200)
            body(post(restricted, "/auth/password", {"current_password": member["initial_password"], "new_password": "Synthetic!member-2026"}), 200)
            visible = restricted.get("/api/v1/agents/recycle-bin")
            assert visible.status_code == 403 or (visible.status_code == 200 and not visible.json()["items"])
            assert post(restricted, f"/agents/recycle-bin/workflow/{private_workflow['id']}/restore", {}).status_code in (403, 404)
            assert restricted.get(control).status_code == 403
        finally:
            restricted.close()

        with SessionLocal() as database:
            original = database.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run["id"]))
            ended = json.loads(original.payload)
            ended["updated_at"] = (now() - timedelta(days=120)).isoformat()
            original.payload = json.dumps(ended)
            other_id = new_id("run")
            unresolved = {**ended, "id": other_id, "status": "outcome_unknown"}
            database.add(Resource(kind="run",resource_id=other_id,owner_id=original.owner_id,
                tenant_id=original.tenant_id,workspace_id=original.workspace_id,payload=json.dumps(unresolved),
                created_at=ended["updated_at"],updated_at=ended["updated_at"]))
            database.commit()
        retention = body(client.get("/api/v1/runs/retention-summary"), 200)
        assert retention["retention_days"] == 90 and retention["completed_over_age"] == retention["protected_over_age"] == 1
        assert body(client.get("/api/v1/runs/" + run["id"]), 200)["outputs"] == run["outputs"]
    print("PASS archive, history, unresolved and publication protections, tool origin revisions and retention report")


if __name__ == "__main__":
    check()
