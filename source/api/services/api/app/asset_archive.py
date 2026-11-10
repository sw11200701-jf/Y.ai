"""Reversible archive markers for user-managed assets. Original payloads are immutable here."""
import json
from fastapi import APIRouter, Depends
from sqlalchemy import String, cast, exists, func, select, true
from sqlalchemy.orm import aliased
from app.context import get_context
from app.db import Resource, _resource_query, get_session, utc_now
from app.errors import AppError

KINDS = frozenset({"workflow", "agent"})
MARKER = "asset_archive"
router = APIRouter(tags=["asset-archive"])


def archive_exists():
    marker = aliased(Resource)
    return exists().where(marker.kind == MARKER, marker.tenant_id == Resource.tenant_id,
                          marker.workspace_id == Resource.workspace_id, marker.resource_id == cast(Resource.id, String))


def live_asset(kind):
    return ~archive_exists() if kind in KINDS else true()


def _marker(session, row):
    return session.scalar(select(Resource).where(Resource.kind == MARKER, Resource.tenant_id == row.tenant_id,
        Resource.workspace_id == row.workspace_id, Resource.resource_id == str(row.id)))


def _workspace(context):
    return (Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id)


def _candidates(session, context, kind, identifier):
    return session.scalars(select(Resource).where(*_workspace(context), Resource.kind == kind,
        func.instr(Resource.payload, json.dumps(identifier)) > 0))


def _graph_agents(graph):
    for node in graph.get("nodes", []):
        config = node.get("config", {})
        if node.get("type") == "agent" and isinstance(config.get("agent_id"), str):
            yield config["agent_id"]
        if node.get("type") in ("iteration", "loop") and isinstance(config.get("body"), dict):
            yield from _graph_agents(config["body"])


def _version_active(session, context, payload):
    return payload.get("status") == "published" and session.scalar(select(Resource.id).where(
        *_workspace(context), Resource.kind == "agent", Resource.resource_id == payload.get("agent_id"), live_asset("agent"))) is not None


def _bundle_uses(bundle, kind, identifier):
    source = bundle.get("source_identity", {})
    return (source.get("kind") == kind and source.get("id") == identifier) or any(
        item.get("kind") == kind and item.get("resource_id") == identifier for item in bundle.get("dependency_manifest", []))


def _published_reference(session, context, kind, identifier):
    from app.platform_api import _row
    for row in _candidates(session, context, "platform_publication", identifier):
        publication = json.loads(row.payload)
        if not _bundle_uses(publication.get("bundle", {}), kind, identifier):
            continue
        config = _row(session, "platform_api_config", publication.get("config_id"), context.tenant_id, context.workspace_id)
        if config:
            value = json.loads(config.payload)
            if value.get("enabled") and row.resource_id in value.get("versions", []):
                return True
    from sqlalchemy import inspect
    if not inspect(session.connection()).has_table("external_api_versions"):
        return False
    from app.external_api.models import Application, ApplicationVersion
    bundles = session.scalars(select(ApplicationVersion.bundle).join(Application, Application.id == ApplicationVersion.application_id)
        .where(Application.tenant_id == context.tenant_id, Application.workspace_id == context.workspace_id,
               Application.status == "active", ApplicationVersion.status == "published",
               func.instr(ApplicationVersion.bundle, json.dumps(identifier)) > 0))
    return any(_bundle_uses(json.loads(bundle), kind, identifier) for bundle in bundles)


def _running_reference(session, context, kind, identifier):
    from app.execution.contracts import ACTIVE_STATUSES, WAITING_STATUSES
    for row in _candidates(session, context, "run", identifier):
        run = json.loads(row.payload)
        if run.get("status") not in ACTIVE_STATUSES | WAITING_STATUSES:
            continue
        request = run.get("request", {})
        identity = run.get("execution_identity", {})
        if kind == "workflow":
            if identifier in (request.get("workflow_id"), identity.get("workflow_id"), run.get("workflow_snapshot", {}).get("id")):
                return True
        else:
            if identifier in (request.get("agent_id"), request.get("draft_agent_id"), identity.get("agent_id")):
                return True
            if identifier in _graph_agents(run.get("workflow_snapshot", {})) or any(
                config.get("id") == identifier for config in run.get("agent_snapshots", {}).values()):
                return True
        if any(item.get("kind") == kind and item.get("resource_id") == identifier for item in run.get("source_resource_refs", [])):
            return True
    return False


def _blocks(session, context, kind, identifier):
    if _running_reference(session, context, kind, identifier):
        return "仍有运行正在处理、等待或待核查，请先处理该运行"
    if _published_reference(session, context, kind, identifier):
        return "仍被已启用的应用或 API 发布版本引用，请先停用相关发布"
    if kind == "workflow":
        if any(not _marker(session, row) and json.loads(row.payload).get("main_workflow_id") == identifier
               for row in _candidates(session, context, "agent", identifier)):
            return "仍是智能体的主工作流，请先改绑或归档该智能体"
    elif any(not _marker(session, row) and identifier in _graph_agents(json.loads(row.payload))
             for row in _candidates(session, context, "workflow", identifier)):
        return "仍被工作流中的智能体节点引用，请先调整相关工作流"
    for row in _candidates(session, context, "agent_version", identifier):
        value = json.loads(row.payload)
        if not _version_active(session, context, value):
            continue
        if kind == "workflow" and (value.get("config") or {}).get("main_workflow_id") == identifier:
            return "仍被可用的智能体发布版本引用，请先归档该智能体"
        if kind == "agent" and identifier in _graph_agents(value.get("workflow_snapshot") or {}):
            return "仍被可用的智能体发布版本引用，请先归档引用方"
    return None


def _row(session, context, kind, identifier):
    from app.plugins.permissions import require_page
    require_page(context, session, "page.ai")
    if kind not in KINDS:
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    row = session.scalar(_resource_query(session, kind, identifier, context, "write"))
    if row is None:
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    return row


def _view(row, marker):
    value = json.loads(row.payload)
    return {"id": row.resource_id, "kind": row.kind, "name": value.get("name") or row.resource_id,
            "archived_at": marker.created_at}


def _check_restore(session, context, row):
    payload = json.loads(row.payload)
    references = [("agent", identifier) for identifier in _graph_agents(payload)] if row.kind == "workflow" else []
    if row.kind == "agent":
        if payload.get("main_workflow_id"):
            references.append(("workflow", payload["main_workflow_id"]))
        for version in _candidates(session, context, "agent_version", row.resource_id):
            snapshot = json.loads(version.payload)
            if snapshot.get("agent_id") == row.resource_id and snapshot.get("status") == "published":
                if (identifier := (snapshot.get("config") or {}).get("main_workflow_id")):
                    references.append(("workflow", identifier))
                references.extend(("agent", identifier) for identifier in _graph_agents(snapshot.get("workflow_snapshot") or {}))
    for kind, identifier in set(references):
        dependency = session.scalar(select(Resource).where(*_workspace(context), Resource.kind == kind, Resource.resource_id == identifier))
        if dependency is not None and dependency.id != row.id and _marker(session, dependency):
            raise AppError("ASSET_DEPENDENCY_ARCHIVED", "此资源仍依赖回收站中的工作流或智能体，请先恢复依赖", 409)


@router.get("/agents/recycle-bin")
def archived_assets(session=Depends(get_session), context=Depends(get_context)):
    from app.plugins.permissions import require_page
    require_page(context, session, "page.ai")
    items = []
    for kind in sorted(KINDS):
        for row in session.scalars(_resource_query(session, kind, None, context, "write").where(archive_exists())):
            marker = _marker(session, row)
            if marker is not None:
                items.append(_view(row, marker))
    return {"items": sorted(items, key=lambda item: (item["archived_at"], item["id"]), reverse=True)}


async def _change(kind, identifier, restore, context, session):
    from app.runner.service import scheduler
    from app.platform_api import write_lock
    from app.rbac import audit
    async with scheduler.admission_lock:
        write_lock(session)
        row = _row(session, context, kind, identifier)
        marker = _marker(session, row)
        if restore:
            if marker is None:
                return {"restored": True}
            _check_restore(session, context, row)
            session.delete(marker)
            audit(session, context, "asset.restore", kind, identifier)
            session.commit()
            return {"restored": True}
        if marker is None:
            if reason := _blocks(session, context, kind, identifier):
                raise AppError("ASSET_IN_USE", reason, 409)
            now = utc_now()
            marker = Resource(kind=MARKER, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                resource_id=str(row.id), owner_id=context.operator_id, payload="{}", created_at=now, updated_at=now)
            session.add(marker)
            audit(session, context, "asset.archive", kind, identifier)
            session.commit()
        return _view(row, marker)


@router.post("/agents/recycle-bin/{kind}/{identifier}/archive")
async def archive(kind: str, identifier: str, session=Depends(get_session), context=Depends(get_context)):
    return await _change(kind, identifier, False, context, session)


@router.post("/agents/recycle-bin/{kind}/{identifier}/restore")
async def restore(kind: str, identifier: str, session=Depends(get_session), context=Depends(get_context)):
    return await _change(kind, identifier, True, context, session)
