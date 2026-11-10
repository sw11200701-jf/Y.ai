"""Exercise database operations through the public platform contract."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os

from fastapi.testclient import TestClient

from app.main import app
from model_network_smoke import ORIGIN, body, post
from platform_api_smoke import PREFIX, key, token


def check():
    assert os.environ.get("FINANCE_DATA_DIR")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53000)) as owner, closing(TestClient(app, base_url=ORIGIN)) as client:
        user = body(post(owner, "/auth/setup", {"username": "database_owner", "password": "FixtureOnly!893"}), 201)["user"]
        workspace = user["workspace_id"]
        root = f"{PREFIX}/spaces/{workspace}"

        def credential(**grant):
            return {"Authorization": "Bearer " + token(client, key(owner, workspace, **grant))}

        wide = credential(access="write", all_resources=True, allow_create=True)
        read = credential(access="read", all_resources=True)

        def call(method, path, status=200, *, auth=wide, **kwargs):
            response = client.request(method, root + path, headers={**auth, **kwargs.pop("headers", {})}, **kwargs)
            assert response.status_code == status, (method, path, status, response.status_code, response.text[:550])
            return response

        def matched(response):
            return {"If-Match": response.headers["etag"]}

        contract = body(client.get(PREFIX + "/openapi.json", headers=wide), 200)["paths"]
        assert {"get", "post"} <= contract[PREFIX + "/spaces/{workspace_id}/databases"].keys()
        assert {"get", "patch", "delete"} <= contract[PREFIX + "/spaces/{workspace_id}/database/tables/{table_id}"].keys()
        assert not call("GET", "/databases").json()["items"]
        call("POST", "/databases", 403, auth=read, json={"name": "Denied"})
        created = call("POST", "/databases", 201, json={"name": "Synthetic DB", "description": "Fixtures"})
        database_id = created.json()["id"]
        db = f"/databases/{database_id}"
        assert call("GET", "/databases", auth=read).json()["items"][0]["id"] == database_id
        unrelated = credential(access="read")
        assert not call("GET", "/databases", auth=unrelated).json()["items"]
        call("GET", db, 404, auth=unrelated)
        call("PATCH", db, 428, json={"name": "Renamed"})
        changed = call("PATCH", db, headers=matched(created), json={"name": "Renamed"})
        assert changed.json()["description"] == "Fixtures"
        call("PATCH", db, 412, headers=matched(created), json={"description": "Stale"})
        assert call("GET", db, auth=read).json()["name"] == "Renamed"

        scoped = credential(access="write", allow_create=True, resources=[
            {"kind": "database", "resource_id": database_id, "access": "write"},
        ])
        draft = call("POST", db + "/tables", 201, auth=scoped, json={"name": "Ledger", "schema_fields": [
            {"name": "code", "type": "string"}, {"name": "amount", "type": "integer", "required": True},
        ]})
        table_id = draft.json()["id"]
        table = f"/database/tables/{table_id}"
        call("POST", table + "/query", 422, json={})
        updated = call("PATCH", table, headers=matched(draft), auth=scoped, json={"description": "Verified ledger"})
        assert updated.json()["name"] == "Ledger" and updated.json()["schema_fields"][1]["name"] == "amount"
        call("PATCH", table, 404, auth=read, headers=matched(updated), json={"name": "Denied"})
        call("DELETE", table, 404, auth=read, headers=matched(updated))

        original = b"code,amount\n001,10\n002,20\n"
        uploaded = call("POST", table + "/imports", 201, auth=scoped, files={"file": ("ledger.csv", original)}).json()
        batch = f"/database/batches/{uploaded['id']}"
        assert call("GET", table + "/batches", auth=scoped).json()["total"] == 1
        assert call("GET", batch, auth=scoped).json()["quality"]["valid_row_count"] == 2
        call("GET", batch, 404, auth=read)
        first = call("POST", batch + "/publish", auth=scoped).json()["id"]
        corrected = call("POST", batch + "/revisions", 201, auth=scoped,
                         json={"corrections": [{"row": 2, "column": "amount", "value": 15}]}).json()
        assert call("POST", table + "/query", auth=read, json={}).json()["rows"][0]["amount"] == 10
        second = call("POST", f"/database/batches/{corrected['id']}/publish", auth=scoped).json()["id"]
        assert first != second and call("GET", table + "/versions", auth=read).json()["total"] == 2
        assert call("GET", table + "/dictionary", auth=read).json()["version_id"] == second
        assert call("POST", table + "/query", auth=read, json={}).json()["rows"][0] == {"code": "001", "amount": 15}
        assert call("POST", table + "/query", auth=read, json={"version_id": first}).json()["rows"][0]["amount"] == 10
        download = f"/database/files/{uploaded['file_id']}/download"
        assert call("GET", download, auth=read).content == original
        csv = call("POST", table + "/export", auth=read)
        assert csv.headers["X-Data-Version"] == second and "001,15" in csv.content.decode("utf-8-sig")

        restricted = credential(access="write", resources=[
            {"kind": "database", "resource_id": database_id, "access": "write"},
        ])
        assert call("DELETE", db, 403, headers=matched(changed), auth=restricted).json()["error"]["code"] == "DATA_CHILD_SCOPE_DENIED"
        deleted_db = call("DELETE", db, headers=matched(changed))
        assert not call("GET", "/databases", auth=read).json()["items"]
        assert not call("GET", "/resources?kind=data_table", auth=read).json()["items"]
        assert not body(owner.get("/api/v1/databases"), 200)["items"]
        call("GET", db, 404)
        call("POST", table + "/query", 404, json={"version_id": first})
        call("POST", batch + "/publish", 404)
        call("GET", download, 404)
        assert call("GET", "/database/recycle-bin", auth=unrelated).json()["total"] == 0
        assert call("GET", "/database/recycle-bin").json()["items"][0]["etag"] == deleted_db.headers["etag"]
        call("POST", db + "/restore", 428)
        call("POST", db + "/restore", 412, headers=matched(changed))
        call("POST", db + "/restore", headers=matched(deleted_db))
        assert call("POST", table + "/query", json={}).json()["version_id"] == second

        current = call("GET", table)
        table_etag = matched(current)
        call("DELETE", table, 428)

        def mutation(name):
            return client.patch(root + table, headers={**wide, **table_etag}, json={"name": name}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(mutation, ("Concurrent A", "Concurrent B"))) == [200, 412]
        call("DELETE", table, 412, headers=table_etag)
        deleted = call("DELETE", table, headers=matched(call("GET", table)))
        assert call("DELETE", table).headers["etag"] == deleted.headers["etag"]
        assert not call("GET", db + "/tables").json()["items"]
        assert call("GET", "/database/recycle-bin", params={"database_id": database_id}).json()["items"][0]["id"] == table_id
        call("GET", table, 404)
        call("POST", table + "/restore", headers=matched(deleted))
        assert call("POST", table + "/query", json={}).json()["version_id"] == second
        assert call("GET", download).content == original
        imported = call("POST", db + "/imports", 201, files={"file": ("auto.csv", original)}).json()
        assert imported["requires_publish"] and imported["table"]["id"] != table_id

        csrf = body(owner.get("/api/v1/auth/csrf"), 200)["csrf_token"]
        policy = f"/api/v1/data-access/data_table/{table_id}"
        body(owner.put(policy, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json={"grants": [{
            "subject_type": "user", "subject_id": user["id"], "effect": "restrict",
            "actions": ["use", "maintain", "download_original", "export_detail"],
            "row_scope": {"template": "companies", "field": "code", "ids": ["001"]},
            "columns": {"hide": ["amount"]},
        }]}), 200)
        restricted_rows = call("POST", table + "/query", auth=read, json={}).json()["rows"]
        assert restricted_rows == [{"code": "001"}]
        restricted_csv = call("POST", table + "/export", auth=read).content.decode("utf-8-sig")
        assert restricted_csv.splitlines() == ["code", "001"]
        call("GET", download, 403, auth=read)
        call("DELETE", table, 403, headers=matched(call("GET", table)))
        body(owner.put(policy, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json={"grants": []}), 200)

        spare = f"/database/tables/{imported['table']['id']}"
        spare_etag = matched(call("GET", spare))
        with ThreadPoolExecutor(max_workers=2) as pool:
            removal = pool.submit(client.delete, root + spare, headers={**wide, **spare_etag})
            replacement = pool.submit(client.post, root + spare + "/imports", headers=wide,
                files={"file": ("new.csv", b"code,amount\n003,30\n")})
            pair = (removal.result().status_code, replacement.result().status_code)
        assert pair in ((200, 404), (412, 201)), pair


if __name__ == "__main__":
    check()
    print("PLATFORM_DATABASE_SMOKE_OK")
