"""Exercise account boundaries, safe directories and platform audit records."""
from contextlib import ExitStack, closing
import json
import os

from fastapi.testclient import TestClient

from app.main import app
from model_network_smoke import ORIGIN, body, post
from platform_api_smoke import PREFIX
from workspace_publication_smoke import workflow


def check():
    assert os.environ.get("FINANCE_DATA_DIR")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as owner, ExitStack() as stack:
        administrator = body(post(owner, "/auth/setup", {"username": "system_owner", "password": "SystemFixture!858"}), 201)["user"]
        workspace = administrator["workspace_id"]

        def login(name, role, port):
            user = body(post(owner, "/users", {"username": name, "role_id": role}), 201)
            caller = stack.enter_context(closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", port))))
            body(post(caller, "/auth/login", {"username": name, "password": user["initial_password"]}), 200)
            assert body(caller.get("/api/v1/agents"), 403)["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
            body(post(caller, "/auth/password", {"current_password": user["initial_password"],
                "new_password": "SystemFixture!784"}), 200)
            return caller, user

        def put(caller, path, value, method="PUT"):
            token = body(caller.get("/api/v1/auth/csrf"), 200)["csrf_token"]
            return caller.request(method, "/api/v1" + path, json=value, headers={"Origin": ORIGIN, "X-CSRF-Token": token})

        admin, _ = login("system_admin", "admin", 53001)
        member, person = login("system_member", "member", 53002)
        body(member.get("/api/v1/agents"), 200)
        body(member.get("/api/v1/users"), 403)
        body(member.get("/api/v1/workspace-logs"), 403)
        body(admin.get("/api/v1/users"), 200)
        body(admin.get("/api/v1/workspace-logs"), 403)
        assert body(post(admin, "/users", {"username": "bad_admin", "role_id": "super_admin"}), 403)["error"]["code"] == "ROLE_GRANT_DENIED"
        assert body(put(owner, f"/users/{administrator['id']}", {"active": False}, "PATCH"), 409)["error"]["code"] == "LAST_SUPER_ADMIN"
        assert body(owner.post("/api/v1/workflows", json=workflow("No CSRF", "Blocked")), 403)["error"]["code"] == "CSRF_REJECTED"

        saved = body(post(owner, "/workflows", workflow("Published fixture", "Original")), 201)
        identifier = saved["id"]
        path = f"/workflows/{identifier}"
        access = f"/resources/workflow/{identifier}/access"
        body(put(owner, access, {"grants": [{"user_id": person["id"], "permission": "view"}]}), 200)
        cards = body(member.get("/api/v1/workflows"), 200)["items"]
        item = next(card for card in cards if card["id"] == identifier)
        assert item["metadata_only"] and item["access"]["read"] is False and "nodes" not in item
        body(member.get("/api/v1" + path), 404)
        body(post(owner, path + "/publish", {"visibility": "public", "expected_graph_sha256": saved["graph_sha256"]}), 201)
        body(put(owner, path, {**workflow("Unpublished draft name", "Draft only"),
            "expected_graph_sha256": saved["graph_sha256"]}), 200)
        details = body(member.get("/api/v1" + path), 200)
        assert details["name"] == "Published fixture" and not details["access"]["edit"]

        secondary = body(post(owner, "/platform/spaces", {"name": "Synthetic secondary space"}), 201)["id"]
        members_path = f"/platform/spaces/{secondary}/members"
        roles = body(owner.get(f"/api/v1/platform/spaces/{secondary}/roles"), 200)["items"]
        role = next(item["id"] for item in roles if item["key"] == "member")
        body(put(owner, members_path, {"user_id": person["id"], "role_id": role}), 200)
        receipt = body(post(owner, "/platform/keys", {"name": "Synthetic platform scope", "owner_id": person["id"],
            "spaces": [{"workspace_id": value, "access": "read", "all_resources": True}
                for value in (workspace, secondary)]}), 201)
        exchange = member.post(PREFIX + "/token", json={"api_key": receipt["api_key"]})
        access_token = body(exchange, 200)["access_token"]
        bearer = {"Authorization": "Bearer " + access_token}
        directory = PREFIX + f"/spaces/{workspace}/resources"
        resources = body(member.get(directory, params={"kind": "workflow"}, headers=bearer), 200)["items"]
        assert next(item for item in resources if item["id"] == identifier)["name"] == "Published fixture"
        assert body(member.get(directory + f"/workflow/{identifier}", headers=bearer), 200)["name"] == "Published fixture"
        body(member.get(PREFIX + "/spaces/another-workspace/resources", params={"kind": "workflow"}, headers=bearer), 404)
        other_directory = PREFIX + f"/spaces/{secondary}/resources"
        assert body(member.get(other_directory, params={"kind": "workflow"}, headers=bearer), 200)["items"] == []
        assert body(member.get(PREFIX + f"/spaces/{secondary}/resources/workflow/{identifier}", headers=bearer), 404)
        body(put(owner, members_path, {"user_id": person["id"], "role_id": role, "active": False}), 200)
        body(member.get(other_directory, params={"kind": "workflow"}, headers=bearer), 404)
        assert {item["id"] for item in body(member.get(PREFIX + "/spaces", headers=bearer), 200)["items"]} == {workspace}

        revoked = member.post(PREFIX + "/token/revoke", headers=bearer)
        body(revoked, 200)
        for response, action in ((exchange, "POST /api/platform/v1/token"), (revoked, "POST /api/platform/v1/token/revoke")):
            logs = body(owner.get("/api/v1/workspace-logs",
                params={"category": "operation", "reference": response.headers["x-request-id"]}), 200)["items"]
            assert any(item["actor"] == receipt["key"]["id"] and item["action"] == action for item in logs), logs
            assert access_token not in json.dumps(logs) and receipt["api_key"] not in json.dumps(logs)
        body(member.get(directory, params={"kind": "workflow"}, headers=bearer), 401)

        app_receipt = body(post(owner, "/applications", {"name": "Workspace fixture app", "type": "workflow"}), 201)
        preview = post(owner, f"/applications/{app_receipt['application_id']}/versions/preview",
            {"source": {"kind": "workflow", "id": identifier}})
        assert body(preview, 409)["error"]["code"] == "APP_WORKSPACE_ASSET_REQUIRES_PLATFORM"

        archived = body(post(owner, "/workflows", workflow("Recycled fixture", "Not visible")), 201)
        body(post(owner, f"/agents/recycle-bin/workflow/{archived['id']}/archive", {}), 200)
        manage = f"/api/v1/platform/spaces/{workspace}/resources"
        listed = body(owner.get(manage, params={"kind": "workflow"}), 200)["items"]
        assert archived["id"] not in {item["id"] for item in listed}


if __name__ == "__main__":
    check()
    print("SYSTEM_PERMISSION_SMOKE_OK")
