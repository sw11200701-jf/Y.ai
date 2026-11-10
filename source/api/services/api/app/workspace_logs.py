"""Workspace-scoped, redacted operational logs and existing audit history."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select

from app.auth import validate_context
from app.auth_models import Installation, User
from app.context import get_context
from app.db import OwnershipAudit, RunEventRecord, SessionLocal, get_session
from app.errors import AppError
from app.settings import settings

router = APIRouter(tags=["workspace-logs"])
LOGIN_ROUTES = {"/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/setup"}
LOGS_PATH = "/api/v1/workspace-logs"
Kind = Literal["all", "error", "login", "operation"]
CLIENT_CODES = Literal["CONVERSATION_LIST_TIMEOUT", "CONVERSATION_LIST_NETWORK", "PAGE_RENDER_FAILED"]


def safe_code(value, fallback=None):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value) else fallback


def init_logs():
    with closing(connect()) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS workspace_logs (
                id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, workspace TEXT NOT NULL,
                actor TEXT, category TEXT NOT NULL, source TEXT NOT NULL, action TEXT NOT NULL,
                status INTEGER, code TEXT, request_id TEXT, created_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS log_workspace_time ON workspace_logs(tenant,workspace,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS log_workspace_category ON workspace_logs(tenant,workspace,category,id DESC)")


def connect():
    return sqlite3.connect(settings.data_dir / "workspace-logs.sqlite3", timeout=0.5)


def actor_scope(user):
    return user.tenant_id, user.workspace_id, user.id


def _installation_scope():
    with SessionLocal() as session:
        installation = session.get(Installation, 1)
        user = session.get(User, installation.admin_user_id) if installation else None
        return (user.tenant_id, user.workspace_id, None) if user else None


async def record_request(request: Request, status: int):
    # No path parameters, submitted values, headers, response bodies or exception text.
    path = request.url.path
    if path == LOGS_PATH or path.startswith(LOGS_PATH + "/"):
        return
    login = path in LOGIN_ROUTES and request.method == "POST"
    context = getattr(request.state, "business_context", None)
    scope = getattr(request.state, "log_actor", None)
    if scope is None and context and not (login and path != "/api/v1/auth/logout"):
        scope = context.tenant_id, context.workspace_id, context.operator_id
    if not login and (scope is None or status < 400 and request.method not in {"POST", "PUT", "PATCH", "DELETE"}):
        return
    route = getattr(request.scope.get("route"), "path", None)
    if route and not route.startswith("/api/"):
        prefix = next((prefix for prefix in ("/api/v1", "/api/open/v1", "/api/platform/v1")
                       if path.startswith(prefix + "/")), "")
        route = prefix + route
    category = "login" if login else "error" if status >= 400 else "operation"
    code = safe_code(getattr(request.state, "model_test_error_code", None), f"HTTP_{status}") if status >= 400 else None
    action = f"{request.method} {path if login else route or 'API'}"
    from anyio.to_thread import run_sync
    await run_sync(_record, scope, category, action, status, code, request.state.request_id)


def _record(scope, category, action, status, code, request_id):
    try:
        if scope is None:
            scope = _installation_scope()
        if scope is None:
            return
        _write(scope, category, "request", action, status, code, request_id)
    except Exception:
        logging.getLogger(__name__).warning("Workspace request log could not be recorded")


def _write(scope, category, source, action, status, code, request_id):
    now = datetime.now(timezone.utc)
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("""
            SELECT request_id FROM workspace_logs
            WHERE tenant=? AND workspace=? AND actor=? AND source='browser' AND code=? AND created_at>=?
            ORDER BY id DESC LIMIT 1
        """, (*scope, code, (now - timedelta(seconds=30)).isoformat())).fetchone() if source == "browser" else None
        if previous:
            return previous[0]
        db.execute("""
            INSERT INTO workspace_logs(tenant,workspace,actor,category,source,action,status,code,request_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (*scope, category, source, action, status, code, request_id, now.isoformat()))
        latest = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if latest % 100 == 0:
            db.execute("DELETE FROM workspace_logs WHERE id<=? OR created_at<?",
                       (latest - 100000, (now - timedelta(days=90)).isoformat()))
    return request_id


class ClientError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: CLIENT_CODES


@router.post("/workspace-logs/client-error", status_code=201)
def client_error(body: ClientError, request: Request, context=Depends(get_context), session=Depends(get_session)):
    user = validate_context(context, session)
    try:
        reference = _write(actor_scope(user), "error", "browser", "页面异常", None, body.code, request.state.request_id)
    except sqlite3.Error:
        logging.getLogger(__name__).warning("Workspace browser log could not be recorded")
        reference = None
    return {"request_id": reference}


@router.get("/workspace-logs")
def workspace_logs(category: Kind = "all", page: int = Query(1, ge=1, le=100),
                   page_size: int = Query(30, ge=1, le=100),
                   reference: str = Query("", max_length=100, pattern=r"^[A-Za-z0-9_-]*$"),
                   context=Depends(get_context), session=Depends(get_session)):
    user = validate_context(context, session)
    if user.role != "super_admin":
        raise AppError("FORBIDDEN", "只有超级管理员可以查看系统日志", 403)
    # Take the first N candidates from each source, then merge their shared time order.
    offset = (page - 1) * page_size
    limit = offset + page_size + 1
    params = [context.tenant_id, context.workspace_id]
    where = "tenant=? AND workspace=?"
    if category != "all":
        where += " AND " + ("(category='error' OR (category='login' AND status>=400))" if category == "error" else "category=?")
        if category != "error":
            params.append(category)
    if reference:
        where += " AND (request_id=? OR code=? OR ('request-'||id)=?)"
        params.extend((reference.lower(), reference.upper(), reference))
    with closing(connect()) as db:
        db.row_factory = sqlite3.Row
        items = [dict(row) for row in db.execute(
            f"""SELECT 'request-'||id AS id,actor,category,source,action,status,code,request_id,created_at
                FROM workspace_logs WHERE {where} ORDER BY id DESC LIMIT ?""", (*params, limit))]
    if category in ("all", "operation"):
        audit = OwnershipAudit
        history = select(audit.id, audit.actor_id, audit.action, audit.resource_kind, audit.created_at).join(
            User, User.id == audit.actor_id).where(User.tenant_id == context.tenant_id,
            User.workspace_id == context.workspace_id)
        if reference:
            history = history.where(audit.id == reference)
        history = history.order_by(audit.created_at.desc(), audit.id.desc()).limit(limit)
        items.extend({"id": row.id, "actor": row.actor_id, "category": "operation", "source": "audit",
                      "action": f"{row.action} · {row.resource_kind}", "created_at": row.created_at}
                     for row in session.execute(history))
    if category in ("all", "error"):
        event = RunEventRecord
        event_type = func.json_extract(event.payload, "$.type")
        created = func.json_extract(event.payload, "$.created_at")
        code = func.json_extract(event.payload, "$.data.error.code")
        history = select(event.id, event.run_id, event_type.label("action"), created.label("created"),
                         code.label("code")).where(event.tenant_id == context.tenant_id,
            event.workspace_id == context.workspace_id,
            event_type.in_(("run.failed", "run.interrupted", "effect.unknown")))
        if reference:
            history = history.where(or_(event.run_id == reference, code == reference.upper()))
        history = history.order_by(created.desc(), event.id.desc()).limit(limit)
        items.extend({"id": f"run-event-{row.id}", "category": "error", "source": "run",
                      "action": row.action, "code": safe_code(row.code), "run_id": row.run_id,
                      "created_at": row.created} for row in session.execute(history) if row.created)
    items.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
    return {"items": items[offset:offset + page_size], "page": page, "page_size": page_size,
            "has_more": page < 100 and len(items) > offset + page_size}
