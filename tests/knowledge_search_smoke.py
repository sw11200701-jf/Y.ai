"""Published-only keyword ranking and reversible document removal smoke test."""
import os

from fastapi.testclient import TestClient

from app.main import app
from app.reporting.document_renderers import render_document
from model_network_smoke import ORIGIN, body, post


def check():
    assert os.environ.get("FINANCE_DATA_DIR"), "Use a new isolated data directory"
    clauses = [
        ("period.txt", "报销时限。出差结束后十日内提交报销材料。"),
        ("lodging.txt", "住宿标准。一线城市每晚不超过600元。住宿费用需提供有效凭证。"),
        ("flight.txt", "飞行时间超过4小时可乘坐经济舱。购票须事先审批。"),
        ("meal.txt", "餐费标准。出差期间按规定报销餐费。"),
    ]
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as client:
        assert body(client.get("/api/v1/auth/status"), 200)["initialized"] is False
        body(post(client, "/auth/setup", {"username": "test_admin", "password": "Synthetic!fixture-2026"}), 201)
        kb = body(post(client, "/knowledge-bases", {"name": "Synthetic travel policy"}), 201)["id"]
        ids, versions = {}, {}
        for filename, text in clauses:
            csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
            doc = body(client.post(f"/api/v1/knowledge-bases/{kb}/documents",
                files={"file": (filename, text.encode(), "text/plain")},
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}), 201)
            identifier = doc["id"]
            version = body(post(client, f"/knowledge/documents/{identifier}/parse", {}), 200)["id"]
            body(post(client, f"/knowledge/documents/{identifier}/publish", {"version_id": version}), 200)
            ids[filename] = identifier
            versions[filename] = version

        def find(query):
            return body(post(client, "/knowledge/search", {"query": query, "knowledge_base_ids": [kb]}), 200)

        for query, expected in [
            ("住宿标准", "lodging.txt"), ("600元", "lodging.txt"), ("住宿费用上限是多少", "lodging.txt"),
            ("酒店一晚最多能报多少钱", "lodging.txt"), ("出差住酒店可以报销多少", "lodging.txt"),
            ("坐飞机的规定", "flight.txt"),
        ]:
            hits = find(query)["hits"]
            assert hits and hits[0]["resource_id"] == ids[expected], (query, [hit["resource_id"] for hit in hits])
        unrelated = find("量子纠缠实验")
        assert not unrelated["hits"] and unrelated["warnings"]

        lodging = ids["lodging.txt"]
        csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        removed = body(client.delete(f"/api/v1/knowledge/documents/{lodging}",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}), 200)
        assert removed["status"] == "deleted"
        assert not find("住宿标准")["hits"]
        restored = body(post(client, f"/knowledge/documents/{lodging}/restore", {}), 200)
        assert restored["status"] == "draft" and not find("住宿标准")["hits"]
        body(post(client, f"/knowledge/documents/{lodging}/publish", {"version_id": versions["lodging.txt"]}), 200)
        assert find("住宿标准")["hits"][0]["resource_id"] == lodging

        provider = body(post(client, "/model-providers", {
            "name": "Planning fixture", "base_url": "http://127.0.0.1:1/v1", "auth_type": "none",
            "allow_private_network": True, "timeout_seconds": 1,
        }), 201)
        chosen = body(post(client, f"/model-providers/{provider['id']}/models", {
            "name": "Planning fixture", "model_name": "fixture", "workspace_use": True,
            "context_window": 32768, "max_output_tokens": 4096,
            "capabilities": {"json_object": True},
        }), 201)
        job_agent = "/api/v1/builtin-agents/builtin_knowledge_chunker"
        model_choice = {"model_config_id": chosen["id"], "model_config_version": chosen["config_version"],
                        "model_execution_mode": "model_default"}
        profile = body(client.get(job_agent + "/document-job-options",
                                  params={"document_id": lodging, **model_choice}), 200)
        assert profile["execution_ready"], profile
        preview = body(post(client, "/builtin-agents/builtin_knowledge_chunker/document-job-budget",
                            {"document_id": lodging, "expected_revision": profile["revision"], **model_choice}), 200)
        assert preview["mode"] == "preview_only"
        assert preview["reservation"]["model_calls"] >= 2 and preview["reservation"]["total_tokens"] > 0
        deck = render_document(tool_id="document.powerpoint.v1", arguments={
            "title": "Travel expenses",
            "slides": [{"title": "Travel expense submission", "bullets": ["Submit approval and receipts within ten business days."]}],
        })
        csrf = body(client.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        presentation = body(client.post(f"/api/v1/knowledge-bases/{kb}/documents?profile=portable-slicing-1.0.0",
            files={"file": ("fixture.pptx", deck.content, deck.mime_type)},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}), 201)
        ppt_id = presentation["id"]
        ppt_profile = body(client.get(job_agent + "/document-job-options",
                                      params={"document_id": ppt_id, **model_choice}), 200)
        request = {"document_id": ppt_id, "expected_revision": ppt_profile["revision"], **model_choice}
        portable = body(post(client, "/builtin-agents/builtin_knowledge_chunker/document-job-budget",
                            {**request, "max_model_calls": 16}), 200)
        assert portable["minimum_model_calls"] == 8
        assert portable["reservation"]["model_calls"] == 16
        limited = body(post(client, "/builtin-agents/builtin_knowledge_chunker/document-job-budget",
                            {**request, "max_model_calls": 7}), 422)
        assert limited["error"]["code"] == "SLICING_BUDGET_INVALID"
        assert not body(client.get("/api/v1/runs"), 200)["items"]
    print("PASS six travel searches, unrelated query, and delete/restore/republish, and cost-free document plan preview")


if __name__ == "__main__":
    check()
