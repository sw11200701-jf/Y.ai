"""Workspace audience, frozen drafts and actual workflow/Agent runs."""
from contextlib import ExitStack, closing
import os
import time

from fastapi.testclient import TestClient

from app.main import app
from model_network_smoke import ORIGIN, body, post
from platform_api_smoke import PREFIX, token


def workflow(name, answer):
    return {"name": name, "schemaVersion": "2.0", "mode": "demo",
        "nodes": [{"id": "start", "type": "start"}, {"id": "answer", "type": "answer", "inputs": {"text": answer}}],
        "edges": [{"source": "start", "target": "answer"}]}


def check():
    assert os.environ.get("FINANCE_DATA_DIR")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as owner, ExitStack() as stack:
        setup = body(post(owner, "/auth/setup", {"username": "publish_owner", "password": "FixtureOnly!893"}), 201)["user"]
        workspace = setup["workspace_id"]

        def member(name, port):
            value = body(post(owner, "/users", {"username": name, "role_id": "member"}), 201)
            client = stack.enter_context(closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.1", port))))
            body(post(client, "/auth/login", {"username": name, "password": value["initial_password"]}), 200)
            body(post(client, "/auth/password", {"current_password": value["initial_password"],
                "new_password": "FixtureOnly!854"}), 200)
            return client, value["id"]

        alice, alice_id = member("publish_alice", 53001)
        bob, bob_id = member("publish_bob", 53002)
        directory = body(owner.get("/api/v1/agents/publication-audience"), 200)
        assert {item["id"] for item in directory["users"]} == {setup["id"], alice_id, bob_id}
        member_role = next(item["id"] for item in directory["roles"] if item["key"] == "member")

        def choices(client):
            return {item["id"]: item for item in body(client.get("/api/v1/agents/conversations"), 200)["items"]}

        def publish_flow(saved, visibility, revision=0, **scope):
            return post(owner, f"/workflows/{saved['id']}/publish", {"visibility": visibility,
                "expected_revision": revision, "expected_graph_sha256": saved["graph_sha256"], **scope})

        def run(client, target):
            created = body(post(client, "/runs", {**target, "prompt": "Fixture message"}), 202)
            for _ in range(160):
                result = body(client.get("/api/v1/runs/" + created["id"]), 200)
                if result["status"] not in ("queued", "running", "resuming"):
                    assert result["status"] == "succeeded", result.get("error")
                    return result["outputs"]
                time.sleep(.05)
            raise AssertionError("Run did not complete")

        draft = body(post(owner, "/workflows", workflow("Public fixture", "First release")), 201)
        flow_id = draft["id"]
        path = f"/api/v1/workflows/{flow_id}"
        assert flow_id not in choices(owner) and alice.get(path).status_code == 404
        assert publish_flow(draft, "partial").status_code == 422
        assert publish_flow(draft, "partial", user_ids=["outside-space"]).status_code == 422
        assert publish_flow(draft, "public").status_code == 201
        assert choices(alice)[flow_id]["kind"] == "workflow" and flow_id in choices(bob)
        assert body(alice.get(path), 200)["access"] == {"view": True, "read": True, "edit": False, "execute": True}
        assert run(alice, {"workflow_id": flow_id})["text"] == "First release"
        assert post(alice, "/runs", {"workflow": draft, "prompt": "Inline denied"}).status_code in (403, 404)

        csrf = body(owner.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        changed = body(owner.put(path, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf,
            "If-Match": draft["graph_sha256"]}, json=workflow("Private draft name", "Second release")), 200)
        assert body(alice.get(path), 200)["name"] == "Public fixture"
        assert run(owner, {"workflow_id": flow_id})["text"] == "First release"
        assert publish_flow(draft, "private", 1).status_code == 409
        assert publish_flow(changed, "partial", 1, user_ids=[alice_id]).status_code == 201
        assert flow_id in choices(alice) and flow_id not in choices(bob)
        assert body(alice.get(path), 200)["publication"]["visibility"] == "partial"
        assert "user_ids" not in alice.get(path).json()["publication"]
        assert run(alice, {"workflow_id": flow_id})["text"] == "Second release"
        assert bob.get(path).status_code == 404
        assert publish_flow(changed, "partial", 2, role_ids=[member_role]).status_code == 201
        assert flow_id in choices(bob)

        publication = body(post(owner, f"/platform/assets/workflow/{flow_id}/api/publish", {}), 201)
        api_key = body(post(owner, "/platform/keys", {"name": "Fixture platform key", "owner_id": bob_id,
            "spaces": [{"workspace_id": workspace, "access": "read", "all_resources": True}]}), 201)
        headers = {"Authorization": "Bearer " + token(owner, api_key["api_key"])}
        api_path = f"{PREFIX}/spaces/{workspace}/publications/{publication['id']}"
        assert owner.get(api_path, headers=headers).status_code == 200
        assert publish_flow(changed, "private", 3).status_code == 201
        assert flow_id not in choices(alice) and flow_id in choices(owner)
        assert alice.get(path).status_code == 404
        assert owner.get(api_path, headers=headers).status_code == 404
        assert post(alice, "/runs", {"workflow_id": flow_id, "prompt": "Denied"}).status_code in (403, 404)

        linked = body(post(owner, "/workflows", workflow("Bound fixture", "Advanced result")), 201)
        agent = body(post(owner, "/agents", {"name": "Workflow assistant", "agent_type": "advanced",
            "main_workflow_id": linked["id"]}), 201)
        agent_id = agent["id"]
        published = body(post(owner, f"/agents/{agent_id}/publish", {"visibility": "public"}), 201)
        assert choices(alice)[agent_id]["workflow_agent"]
        assert linked["id"] not in choices(alice)
        assert run(alice, {"agent_id": agent_id, "agent_version_id": published["id"]})["text"] == "Advanced result"
        assert post(alice, "/runs", {"workflow_id": linked["id"], "prompt": "Denied"}).status_code == 403
        assert body(alice.get(f"/api/v1/agents/{agent_id}/versions"), 200)["items"][0]["id"] == published["id"]
        assert publish_flow(linked, "private", 1).status_code == 201
        assert agent_id not in choices(alice)
        body(post(owner, f"/agents/{agent_id}/publish", {"visibility": "private", "expected_revision": 1}), 201)
        assert agent_id not in choices(alice)
        assert alice.get(f"/api/v1/agents/{agent_id}/versions").status_code == 404


if __name__ == "__main__":
    check()
    print("WORKSPACE_PUBLICATION_SMOKE_OK")
