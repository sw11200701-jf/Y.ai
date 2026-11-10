"""Run against a fresh FINANCE_DATA_DIR and the ordinary API requirements.

Uses only loopback model endpoints and deterministic synthetic content.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import AsyncMock, patch
import socket
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import now
from app.auth_models import AuthSession
from app.errors import AppError
from app.model_catalog import security
from app.session_policy import PERSISTENT
from app.db import SessionLocal
from app.execution.models import ExecutionStep
from app.main import app

ORIGIN = "http://127.0.0.1:3271"


def body(response, status):
    assert response.status_code == status, (response.status_code, response.json())
    return response.json()


def post(client, path, value):
    csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
    return client.post("/api/v1" + path, json=value, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


def model(client, port):
    provider = body(post(client, "/model-providers", {
        "name": f"Loopback fixture {port}", "base_url": f"http://127.0.0.1:{port}/v1",
        "auth_type": "none", "allow_private_network": True, "timeout_seconds": .4,
    }), 201)
    return body(post(client, f"/model-providers/{provider['id']}/models", {
        "name": "Fixture", "model_name": "fixture", "workspace_use": True,
        "context_window": 8192, "max_output_tokens": 1024,
    }), 201)


def run_model(client, config):
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "model", "type": "llm", "config": {
            "model_config_id": config["id"], "model_config_version": config["config_version"],
            "context_budget": 4096, "model_parameters": {"max_tokens": 64},
        }, "inputs": {"prompt": "Synthetic fixture"}},
        {"id": "end", "type": "output", "config": {"presentation": "text"},
         "inputs": {"text": {"$ref": "nodes.model.text"}}},
    ]
    workflow = {"name": "Transport fixture", "schemaVersion": "2.0", "mode": "demo",
                "nodes": nodes, "edges": [{"source": "start", "target": "model"}, {"source": "model", "target": "end"}]}
    run = body(post(client, "/runs", {"workflow": workflow, "inputs": {}, "prompt": "Fixture"}), 202)
    for _ in range(150):
        observed = body(client.get("/api/v1/runs/" + run["id"]), 200)
        if observed["status"] in {"failed", "outcome_unknown", "succeeded"}:
            return observed
        time.sleep(.05)
    raise AssertionError(observed["status"])


def effect_status(run, *, agent_model_only=False):
    query = select(ExecutionStep.status).where(ExecutionStep.run_id == run["id"], ExecutionStep.side_effect.is_(True))
    if agent_model_only:
        query = query.where(ExecutionStep.node_key.contains(":model:"))
    with SessionLocal() as session:
        return list(session.scalars(query))


def check():
    assert os.environ.get("FINANCE_DATA_DIR"), "Use a new isolated data directory"
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as client:
        assert body(client.get("/api/v1/auth/status"), 200)["initialized"] is False
        first = body(client.get("/api/v1/auth/csrf"), 200)
        assert body(client.get("/api/v1/auth/csrf"), 200) == first
        with SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
        credentials = {"username": "test_admin", "password": "Synthetic!fixture-2026"}
        setup = body(post(client, "/auth/setup", credentials), 201)
        assert setup["persistent"] is False and setup["expires_at"]
        with SessionLocal() as session:
            row = session.scalar(select(AuthSession))
            row.expires_at = PERSISTENT
            row.last_seen_at = (now() - timedelta(minutes=31)).isoformat()
            session.commit()
        assert client.get("/api/v1/auth/me").status_code == 401

        # Keep each CSRF cookie paired with the client that obtained it.
        csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        cookies = dict(client.cookies)
        # Independent handles use the running app's lifespan, not six startups.
        def concurrent_login(_):
            request = TestClient(app, base_url=ORIGIN, cookies=cookies, client=("127.0.0.1", 53001))
            try:
                return request.post("/api/v1/auth/login", json=credentials,
                                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code
            finally:
                request.close()

        with ThreadPoolExecutor(max_workers=6) as pool:
            assert list(pool.map(concurrent_login, range(6))) == [200] * 6
        assert post(client, "/auth/login", credentials).status_code == 200

        with socket.socket() as blocked:
            blocked.bind(("127.0.0.1", 0))
            configured = model(client, blocked.getsockname()[1])
            with patch.object(security, "approved_model_address",
                              new=AsyncMock(side_effect=AppError("MODEL_ERROR", "合成解析失败", 502))):
                unresolved = run_model(client, configured)
            assert unresolved["status"] == "failed" and unresolved["error"]["details"]["effect_issued"] is False
            assert effect_status(unresolved) == []

            failed = run_model(client, configured)
            assert failed["status"] == "failed", failed.get("error")
            assert failed["error"]["code"] == "MODEL_UNREACHABLE", failed.get("error")
            assert effect_status(failed) == ["failed"]

            agent = body(post(client, "/agents", {
                "name": "Synthetic transport agent", "agent_type": "simple",
                "model_config_id": configured["id"], "model_config_version": configured["config_version"],
                "context_budget": 4096, "model_parameters": {"max_tokens": 64},
            }), 201)
            debug = body(post(client, f"/agents/{agent['id']}/debug", {"prompt": "Synthetic agent fixture"}), 502)
            assert debug["error"]["code"] == "MODEL_UNREACHABLE", debug
            assert effect_status({"id": debug["error"]["details"]["run_id"]}, agent_model_only=True) == ["failed"]

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(4)
            received = []
            release = threading.Event()

            def hold_response():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    return
                with connection:
                    connection.settimeout(4)
                    received.append(connection.recv(8192))
                    release.wait(4)

            worker = threading.Thread(target=hold_response, daemon=True)
            worker.start()
            try:
                unknown = run_model(client, model(client, listener.getsockname()[1]))
                assert unknown["status"] == "outcome_unknown", unknown.get("error")
                assert unknown["error"]["details"]["cause"] == "MODEL_TIMEOUT"
                assert effect_status(unknown) == ["issued"]
                assert received and received[0].startswith(b"POST /v1/chat/completions")
            finally:
                release.set()
                worker.join(5)
    print("PASS signed CSRF, administrator idle expiry, six concurrent logins, workflow and Agent connection boundaries")


if __name__ == "__main__":
    check()
