"""Workspace publication scope and immutable released content."""
import json
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.orm import aliased

from app.auth_models import AccessRole, User
from app.context import get_context
from app.db import Resource, get_session, utc_now
from app.errors import AppError, register_error_messages

KINDS = {"agent", "workflow"}
MARKER = "workspace_asset_publication"
router = APIRouter(tags=["workspace-publications"])

register_error_messages({
    "ASSET_PUBLISH_OWNER_REQUIRED": "只有资源所有者可以发布并设置公开范围",
    "ASSET_AUDIENCE_INVALID": "请选择当前空间的有效成员或角色",
    "ASSET_PUBLICATION_CONFLICT": "发布范围已更新，请刷新后再提交",
    "ASSET_LINKED_WORKFLOW": "此主流程请通过已发布的智能体使用",
    "ASSET_AGENT_VERSION_REQUIRED": "请先发布工作流引用的智能体，再发布工作流",
    "ASSET_NESTED_WORKFLOW_AGENT": "工作流节点可调用普通智能体。带主流程的智能体请从工作台运行",
})


class Audience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["private", "partial", "public"]
    user_ids: list[str] = Field(default_factory=list, max_length=256)
    role_ids: list[str] = Field(default_factory=list, max_length=64)
    expected_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_selection(self):
        selected = self.user_ids + self.role_ids
        if len(selected) != len(set(selected)) or any(not identifier or len(identifier) > 100 for identifier in selected):
            raise ValueError("公开范围包含无效或重复的成员、角色")
        if (self.visibility == "partial") != bool(selected):
            raise ValueError("部分公开时至少选择一个成员或角色")
        return self


class WorkflowPublish(Audience):
    expected_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def asset_row(session, kind, identifier, tenant, space):
    return session.scalar(select(Resource).where(Resource.kind == kind, Resource.resource_id == identifier,
        Resource.tenant_id == tenant, Resource.workspace_id == space).execution_options(populate_existing=True))


def _marker_match(marker, asset):
    return and_(marker.kind == MARKER, marker.resource_id == cast(asset.id, String),
        marker.tenant_id == asset.tenant_id, marker.workspace_id == asset.workspace_id)


def _parent(session, row):
    return asset_row(session, "agent", json.loads(row.payload).get("agent_id"), row.tenant_id, row.workspace_id)


def state(session, row):
    if row.kind == "agent_version":
        row = _parent(session, row)
    if row is None or row.kind not in KINDS:
        return None
    marker = session.scalar(select(Resource).where(_marker_match(Resource, row)).execution_options(populate_existing=True))
    return json.loads(marker.payload) if marker else None


def allows(session, subject, publication):
    if publication["visibility"] == "public":
        return True
    if publication["visibility"] != "partial":
        return False
    from app.data_access import roles_for_user
    return subject.id in publication["user_ids"] or bool(set(publication["role_ids"]) & roles_for_user(session, subject))


def override(session, subject, row):
    """A managed release replaces grants for other users, without granting edits."""
    publication = state(session, row)
    if publication is None or subject.id == publication["owner_id"]:
        return None
    if row.kind == "agent_version":
        parent = _parent(session, row)
        if parent is None or asset_row(session, "asset_archive", str(parent.id), row.tenant_id, row.workspace_id):
            return False
        version = json.loads(row.payload)
        scope = version.get("workspace_audience")
        if version.get("status") != "published" or not (
                publication.get("agent_version_id") == row.resource_id or scope and allows(session, subject, scope)):
            return False
    return allows(session, subject, publication)


def filter_access(session, subject, kind, access, legacy):
    if kind not in KINDS | {"agent_version"}:
        return legacy
    from app.data_access import roles_for_user
    marker = aliased(Resource)
    asset = aliased(Resource) if kind == "agent_version" else Resource
    match = _marker_match(marker, asset)
    if kind == "agent_version":
        match = and_(match, asset.kind == "agent", asset.tenant_id == Resource.tenant_id,
            asset.workspace_id == Resource.workspace_id, asset.resource_id == func.json_extract(Resource.payload, "$.agent_id"))

    def policy_exists(*conditions):
        return exists(select(marker.id).where(match, *conditions).correlate(Resource))

    owner = func.json_extract(marker.payload, "$.owner_id")
    unmanaged_or_owned = ~policy_exists(owner != subject.id)
    if access == "write":
        return legacy & unmanaged_or_owned
    subject_roles = roles_for_user(session, subject)
    def scope_matches(payload, path):
        members = func.json_each(payload, path + ".user_ids").table_valued("value", joins_implicitly=True)
        roles = func.json_each(payload, path + ".role_ids").table_valued("value", joins_implicitly=True)
        selected = (exists(select(1).select_from(members).where(members.c.value == subject.id)) |
            exists(select(1).select_from(roles).where(roles.c.value.in_(subject_roles))))
        visibility = func.json_extract(payload, path + ".visibility")
        return or_(visibility == "public", and_(visibility == "partial", selected))

    audience = scope_matches(marker.payload, "$")
    if kind == "agent_version":
        archived = aliased(Resource)
        active_parent = ~exists(select(archived.id).where(archived.kind == "asset_archive",
            archived.tenant_id == asset.tenant_id, archived.workspace_id == asset.workspace_id,
            archived.resource_id == cast(asset.id, String)).correlate(asset))
        audience = and_(audience, active_parent, func.json_extract(Resource.payload, "$.status") == "published",
            or_(func.json_extract(marker.payload, "$.agent_version_id") == Resource.resource_id,
                scope_matches(Resource.payload, "$.workspace_audience")))
    return (legacy & unmanaged_or_owned) | policy_exists(owner != subject.id, audience)


def view_payload(session, context, row, value):
    if row.kind == "agent_version":
        value.pop("workspace_audience", None)
        return value
    if row.kind not in KINDS or context.principal_type == "service":
        return value
    publication = state(session, row)
    if publication is None or publication["owner_id"] == context.actor_id:
        return value
    if row.kind == "workflow":
        return publication["workflow"]
    version = asset_row(session, "agent_version", publication["agent_version_id"], row.tenant_id, row.workspace_id)
    if version is None:
        raise AppError("NOT_FOUND", "发布版本不存在", 404)
    return {**json.loads(version.payload)["config"], "status": "published", "latest_version": publication["version"]}


def summary(session, context, row, value=None):
    publication = state(session, row)
    if publication:
        result = {key: publication[key] for key in ("visibility", "version", "revision", "published_at")}
        if publication["owner_id"] == context.actor_id:
            result.update(user_ids=publication["user_ids"], role_ids=publication["role_ids"])
        return {**result, "status": "published"}
    value = value if value is not None else json.loads(row.payload)
    legacy_version = value.get("latest_version") if row.kind == "agent" else None
    if row.kind == "workflow":
        from app.platform_publications import config_id
        config = asset_row(session, "platform_api_config", config_id(row.kind, row.resource_id), row.tenant_id, row.workspace_id)
        if config:
            data = json.loads(config.payload)
            if data.get("enabled"):
                return {"status": "api_published", "visibility": "legacy", "revision": 0}
    return {"status": "published" if legacy_version or value.get("status") == "published" else "draft",
        "visibility": "legacy",
        "version": legacy_version, "revision": 0}


def require_owner(context, row):
    if row is None or row.owner_id != context.actor_id or context.principal_type != "human":
        raise AppError("ASSET_PUBLISH_OWNER_REQUIRED", "只有资源所有者可以发布并设置公开范围", 403)


def _audience_directory(session, context):
    members = select(Resource.payload).where(Resource.kind == "platform_space_member",
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id)
    member_ids = {value["user_id"] for raw in session.scalars(members) if (value := json.loads(raw)).get("active")}
    users = session.scalars(select(User).where(User.tenant_id == context.tenant_id, User.active.is_(True),
        or_(User.workspace_id == context.workspace_id, User.id.in_(member_ids))).order_by(User.username))
    roles = session.scalars(select(AccessRole).where(AccessRole.tenant_id == context.tenant_id,
        AccessRole.workspace_id == context.workspace_id).order_by(AccessRole.name))
    return {"users": [{"id": user.id, "name": user.display_name or user.username} for user in users],
        "roles": [{"id": role.id, "name": role.name, "key": role.key} for role in roles]}


@router.get("/agents/publication-audience")
def audience_options(session=Depends(get_session), context=Depends(get_context)):
    from app.rbac import require_permission
    require_permission(context, session, "page.ai")
    return _audience_directory(session, context)


def prepare(session, context, row, body):
    require_owner(context, row)
    current = state(session, row)
    if (current["revision"] if current else 0) != body.expected_revision:
        raise AppError("ASSET_PUBLICATION_CONFLICT", "发布范围已更新，请刷新后再提交", 409)
    if body.visibility == "partial":
        directory = _audience_directory(session, context)
        if (not set(body.user_ids) <= {item["id"] for item in directory["users"]}
                or not set(body.role_ids) <= {item["id"] for item in directory["roles"]}):
            raise AppError("ASSET_AUDIENCE_INVALID", "请选择当前空间的有效成员或角色", 422)
    return current


def record(session, context, row, body, current, *, version, **snapshot):
    from app.platform_api import _put
    from app.rbac import audit
    scope = audience_fields(body)
    revision = (current["revision"] if current else 0) + 1
    publication = {**scope, "owner_id": row.owner_id, "revision": revision, "version": version,
        "published_at": utc_now(), **snapshot}
    _put(session, MARKER, str(row.id), publication, row.tenant_id, row.workspace_id, row.owner_id)
    audit(session, context, "asset.publish", row.kind, row.resource_id, {"visibility": body.visibility, "version": version})
    return summary(session, context, row)


def audience_fields(body):
    return body.model_dump(include={"visibility", "user_ids", "role_ids"})


def publish_workflow(session, context, workflow_id, body):
    from app.contracts import WorkflowDefinition
    from app.db import get_resource
    from app.execution.node_settings import clean, revision
    from app.platform_api import write_lock
    from app.rbac import require_permission
    from app.runner.validation import validate_workflow
    write_lock(session)
    require_permission(context, session, "page.ai")
    value = get_resource(session, "workflow", workflow_id, context, access="write")
    row = asset_row(session, "workflow", workflow_id, context.tenant_id, context.workspace_id)
    current = prepare(session, context, row, body)
    if body.expected_graph_sha256 != revision(value):
        raise AppError("WORKFLOW_REVISION_CONFLICT", "流程已更新，请重新读取后发布", 409)
    snapshot = clean(value)
    validate_workflow(WorkflowDefinition.model_validate(snapshot))
    pin_agents(session, context, snapshot)
    return record(session, context, row, body, current, version=(current["version"] if current else 0) + 1, workflow=snapshot)


def pin_agents(session, context, workflow):
    """A published workflow always references a published Agent version."""
    from app.db import get_resource, list_resources
    latest = None
    def walk(graph):
        nonlocal latest
        for node in graph.get("nodes", []):
            config = node.get("config", {})
            if node["type"] == "agent":
                identifier = config["agent_id"]
                get_resource(session, "agent", identifier, context)
                if config.get("agent_version_id"):
                    version = get_resource(session, "agent_version", config["agent_version_id"], context)
                else:
                    if latest is None:
                        latest = {}
                        for candidate in list_resources(session, "agent_version", context):
                            agent_id = candidate["agent_id"]
                            if candidate["status"] == "published" and candidate["version"] > latest.get(agent_id, {}).get("version", 0):
                                latest[agent_id] = candidate
                    version = latest.get(identifier)
                if not version or version.get("status") != "published" or version["agent_id"] != identifier:
                    raise AppError("ASSET_AGENT_VERSION_REQUIRED", "请先发布工作流引用的智能体，再发布工作流", 422)
                if version["config"].get("main_workflow_id"):
                    raise AppError("ASSET_NESTED_WORKFLOW_AGENT", "带主流程的智能体请从工作台运行", 422)
                config["agent_version_id"] = version["id"]
            if node["type"] in {"iteration", "loop"}:
                walk(config["body"])
    walk(workflow)


def scope_for(session, context, kind, identifier):
    row = asset_row(session, kind, identifier, context.tenant_id, context.workspace_id)
    return summary(session, context, row) if row else None


def execution_version(session, context, agent_id, requested=None):
    if requested or context.principal_type == "service":
        return requested
    row = asset_row(session, "agent", agent_id, context.tenant_id, context.workspace_id)
    publication = state(session, row) if row else None
    if publication and (context.actor_id != row.owner_id or "resources.execute" not in context.permissions):
        return publication["agent_version_id"]
    return requested


def execution_workflow(session, context, identifier, draft):
    if context.principal_type == "human":
        row = asset_row(session, "workflow", identifier, context.tenant_id, context.workspace_id)
        publication = state(session, row) if row else None
        if publication:
            if publication.get("linked_agent_id"):
                raise AppError("ASSET_LINKED_WORKFLOW", "此主流程请通过已发布的智能体使用", 403)
            return publication["workflow"]
    return draft
