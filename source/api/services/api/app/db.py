import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, UniqueConstraint, select, event, inspect, text, or_, exists
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
import json
from fastapi import Request
from app.settings import settings
from app.contracts import BusinessContext
from app.errors import AppError

def new_id(prefix: str = "res") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(50))

class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "kind", "resource_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    owner_id: Mapped[str] = mapped_column(String(100),default="__legacy_unassigned__",index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(50))
    updated_at: Mapped[str] = mapped_column(String(50))

class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    workspace_id: Mapped[str] = mapped_column(String(100))
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)

class OwnershipAudit(Base):
    __tablename__="ownership_audit"
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    actor_id: Mapped[str]=mapped_column(String(100))
    action: Mapped[str]=mapped_column(String(80))
    resource_kind: Mapped[str]=mapped_column(String(50))
    resource_id: Mapped[str]=mapped_column(String(100))
    details: Mapped[str]=mapped_column(Text)
    created_at: Mapped[str]=mapped_column(String(50))

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}, pool_pre_ping=True)
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(engine, expire_on_commit=False)

def init_db():
    from app import auth_models
    from app import data_models
    from app.execution import models as execution_models
    from app.execution.migration import prepare as prepare_execution
    from app.rbac_migration import prepare,apply,apply_data
    from app.application_migration import CORE_TABLES
    settings.data_dir.mkdir(parents=True,exist_ok=True)
    application_tables=[]
    if settings.application_api_enabled:
        from app import machine_models
        try:
            from app.external_api import models as external_models
        except ModuleNotFoundError as exc:
            if exc.name not in {"app.external_api","app.external_api.models"}:raise
            raise AppError("APP_API_NOT_READY","对外应用存储模块尚未就绪",503) from exc
        from app.application_migration import module_tables,prepare as prepare_application
        application_tables=module_tables(Base.metadata)
        prepare_application(engine,settings.data_dir,application_tables)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    backup_path=prepare(engine,settings.data_dir)
    prepare_execution(engine, settings.data_dir)
    # Additive migration before ORM selects owner_id. No business payload rewrites.
    if inspect(engine).has_table("resources") and "owner_id" not in {c["name"] for c in inspect(engine).get_columns("resources")}:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE resources ADD COLUMN owner_id VARCHAR(100) NOT NULL DEFAULT '__legacy_unassigned__'"))
    Base.metadata.create_all(engine,tables=[table for name,table in Base.metadata.tables.items() if name in CORE_TABLES])
    with SessionLocal() as session:
        if session.get(SchemaVersion, 1) is None:
            session.add(SchemaVersion(version=1, applied_at=utc_now()))
            session.commit()
        if session.get(SchemaVersion, 2) is None:
            # Non-destructive capability migration: resource JSON is version-discriminated
            # at read time. No rewrite/deletion of legacy workflows/runs is necessary.
            session.add(SchemaVersion(version=2, applied_at=utc_now()))
            session.commit()
        if session.get(SchemaVersion,3) is None:
            session.add(SchemaVersion(version=3,applied_at=utc_now()))
            session.commit()
        apply(session,backup_path)
        apply_data(session)
        if session.get(SchemaVersion, 6) is None:
            session.add(SchemaVersion(version=6, applied_at=utc_now()))
        session.commit()
    if settings.application_api_enabled:
        from app.application_migration import apply as apply_application
        apply_application(engine,Base.metadata,application_tables)

def get_session(request: Request = None):
    # The main app owns commit/rollback before sending response headers. Keep the
    # generator API for modules/standalone tests without that middleware.
    managed = getattr(request.state, "db_session", None) if request is not None else None
    if managed is not None:
        yield managed
        return
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

def _resource_query(session, kind, resource_id, context, access="read"):
    from app.auth_models import ResourceGrant
    if getattr(context,"principal_type","human")=="service":
        from app.machine_models import ServiceResourceBinding,ServiceResourceGrant
        owned=exists().where(ServiceResourceBinding.resource_pk==Resource.id,ServiceResourceBinding.principal_id==context.operator_id,
            ServiceResourceBinding.client_id==context.client_id,ServiceResourceBinding.application_id==context.application_id,
            ServiceResourceBinding.version_id==context.application_version_id,ServiceResourceBinding.external_user_hash==context.external_user_hash)
        granted=exists().where(ServiceResourceGrant.resource_pk==Resource.id,ServiceResourceGrant.principal_id==context.operator_id,
            ServiceResourceGrant.version_id==context.application_version_id,ServiceResourceGrant.active==True)
        q=select(Resource).where(Resource.kind==kind,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,
            ((Resource.owner_id==context.operator_id)&owned) if access=="write" else (((Resource.owner_id==context.operator_id)&owned)|granted))
        return q.where(Resource.resource_id==resource_id) if resource_id is not None else q
    from app.auth import validate_context
    from app.resource_policy import expression
    subject=validate_context(context,session)
    q = select(Resource).where(Resource.kind == kind, Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id)
    if not (context.principal_type=='platform' and kind in {'run','dataset','artifact','report_result','agent_version','model_configuration','model_provider'}):
        q=q.where(expression(session,subject,kind,access))
    if context.principal_type=='platform':
        from app.platform_api import token_expression
        q=q.where(token_expression(session,context,kind,access))
    return q.where(Resource.resource_id == resource_id) if resource_id is not None else q

def _document_materialization(kind, previous, payload):
    """First extraction adds derived text; it does not change source authority.

    Any change outside these derived fields still invalidates policy stamps.
    In particular bytes, identity, parent links and active state must agree.
    """
    if kind != "document" or (previous.get("status"), payload.get("status")) != ("uploaded", "extracted"):
        return False
    derived = {"status", "chunks", "chunk_count", "text_length", "chunk_size", "chunk_overlap",
               "extracted_at", "source_refs", "extraction_method", "warnings"}
    return {k: v for k, v in previous.items() if k not in derived} == {k: v for k, v in payload.items() if k not in derived}


def save_resource(session: Session, kind: str, resource_id: str, payload: dict, context: BusinessContext):
    from app.auth import validate_context
    validate_context(context,session)
    from app.execution.builtin_agents import protect_resource
    protect_resource(kind,resource_id,payload)
    row = session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==resource_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    from app.data_access import DATA_KINDS,authorize_data_resource
    if row is not None and kind in DATA_KINDS:
        authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="maintain")
    elif row is not None and session.scalar(_resource_query(session,kind,resource_id,context,"write")) is None:
        raise AppError("NOT_FOUND","Resource not found",404)
    from app.asset_archive import KINDS as ARCHIVABLE, live_asset
    if row is not None and kind in ARCHIVABLE and session.scalar(
            select(Resource.id).where(Resource.id == row.id, live_asset(kind))) is None:
        raise AppError("NOT_FOUND","资源不存在或已移入回收站",404)
    if row is None and kind in DATA_KINDS:
        from app.data_models import DataResourceAlias
        if session.get(DataResourceAlias,(context.tenant_id,context.workspace_id,kind,resource_id)):
            raise AppError("DATA_ALIAS_READ_ONLY","历史数据别名必须通过原有版本化维护入口修改",409)
    if row is None and kind in ("plugin_version","agent_version") and payload.get("status")!="run_snapshot":
        parent_kind="plugin" if kind=="plugin_version" else "agent"
        parent_id=payload.get(parent_kind+"_id")
        if not parent_id or session.scalar(_resource_query(session,parent_kind,parent_id,context,"write")) is None:
            raise AppError("NOT_FOUND","Parent resource not writable",404)
    if getattr(context,"principal_type","human")=="service":
        from app.machine_auth import validate_owned_resource
        if row is not None and not validate_owned_resource(session,row,context):raise AppError("NOT_FOUND","资源不存在或无权访问",404)
        if row is None and (kind not in {"run","dataset","artifact","attachment","document","agent_version","report_result"} or kind=="agent_version" and payload.get("status")!="run_snapshot"):
            raise AppError("APP_SCOPE_DENIED","机器调用不能创建此类管理资源",403)
    if getattr(context,'principal_type','human')=='platform':
        from app.platform_api import authorize_save
        authorize_save(session,context,kind,resource_id,row,payload)
    value = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=lambda x: x.model_dump(mode="json") if hasattr(x, "model_dump") else str(x))
    if row is None:
        row = Resource(kind=kind, resource_id=resource_id, owner_id=(context.actor_id if context.principal_type=="platform" else context.operator_id),tenant_id=context.tenant_id, workspace_id=context.workspace_id, payload=value, created_at=utc_now(), updated_at=utc_now())
        session.add(row)
    else:
        if kind in DATA_KINDS:
            previous=json.loads(row.payload)
            if (previous.get("status"),previous.get("active"))!=(payload.get("status"),payload.get("active")) and not _document_materialization(kind, previous, payload):
                from app.data_access import bump_policy_version
                bump_policy_version(session,context)
        row.payload, row.updated_at = value, utc_now()
    session.flush()
    if getattr(context,"principal_type","human")=="platform":
        from app.platform_api import bind_created
        bind_created(session,context,row)
    if getattr(context,"principal_type","human")=="service":
        from app.machine_auth import bind_resource
        bind_resource(session,row,context)
    return payload

def get_resource(session: Session, kind: str, resource_id: str, context: BusinessContext, access="read", *, include_archived=False):
    from app.auth import validate_context
    validate_context(context,session)
    if not isinstance(resource_id,str) or not resource_id or len(resource_id)>160:
        raise AppError("NOT_FOUND","Resource not found",404)
    from app.data_access import DATA_KINDS,authorize_data_resource
    if kind in DATA_KINDS:
        decision=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="maintain" if access=="write" else "use")
        return json.loads(decision.resource.payload)
    from app.asset_archive import live_asset
    query = _resource_query(session,kind, resource_id, context,access)
    if not include_archived or access != "read":
        query = query.where(live_asset(kind))
    row = session.scalar(query)
    if row is None:
        raise AppError("NOT_FOUND", f"{kind} {resource_id} not found", 404)
    if getattr(context,'principal_type','human')=='platform':
        from app.platform_api import require_token_resource
        require_token_resource(session,context,row,access)
    if getattr(context,"principal_type","human")=="service":
        from app.machine_auth import resource_granted
        if not resource_granted(session,row,context,"use"):raise AppError("NOT_FOUND","资源不存在或无权访问",404)
    if kind in ("run","dataset","artifact","attachment"):
        from app.data_access import require_derivation_access
        require_derivation_access(session=session,context=context,kind=kind,resource_id=resource_id,completed_read=(access=="read"))
    return json.loads(row.payload)

def list_resources(session: Session, kind: str, context: BusinessContext):
    from app.auth import validate_context
    validate_context(context,session)
    from app.data_access import DATA_KINDS,authorize_data_resource
    if kind in DATA_KINDS:
        from app.data_models import DataResourceAlias
        rows=list(session.scalars(select(Resource).where(Resource.kind==kind,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id).order_by(Resource.id.desc())))
        for alias in session.scalars(select(DataResourceAlias).where(DataResourceAlias.tenant_id==context.tenant_id,DataResourceAlias.workspace_id==context.workspace_id,DataResourceAlias.kind==kind)):
            row=session.get(Resource,alias.resource_pk)
            if row and row not in rows:rows.append(row)
        result=[]
        for row in rows:
            try:authorize_data_resource(session=session,context=context,kind=kind,resource_id=row.resource_id)
            except AppError as exc:
                if exc.status_code==404:continue
                raise
            result.append(json.loads(row.payload))
        return result
    result=[]
    from app.asset_archive import live_asset
    for row in session.scalars(_resource_query(session,kind, None, context).where(live_asset(kind)).order_by(Resource.id.desc())):
        if getattr(context,"principal_type","human")=="service":
            from app.machine_auth import resource_granted
            if not resource_granted(session,row,context,"use"):continue
        if kind in ("run","dataset","artifact","attachment"):
            from app.data_access import require_derivation_access
            # Listing is a READ, just like get_resource(access="read"). Only
            # the existing domain completion proofs may cross an old epoch;
            # unknown/incomplete/unproved histories retain the strict fence.
            try:require_derivation_access(session=session,context=context,kind=kind,resource_id=row.resource_id,completed_read=True)
            except AppError as exc:
                if exc.status_code==404:continue
                raise
        result.append(json.loads(row.payload))
    return result

def require_resource_access(session,kind,resource_id,context,permission="read"):
    if permission not in ("read","write"):raise AppError("VALIDATION_ERROR","Unknown resource permission",422)
    return get_resource(session,kind,resource_id,context,access=permission)

def claim_agent_workflow(*,session,agent_id,workflow_id,context):
    from app.auth_models import AgentWorkflowBinding
    from sqlalchemy import delete
    from sqlalchemy.exc import IntegrityError
    require_resource_access(session,"agent",agent_id,context,"write")
    require_resource_access(session,"workflow",workflow_id,context,"write")
    workflow=session.scalar(_resource_query(session,"workflow",workflow_id,context,"write"))
    existing=session.get(AgentWorkflowBinding,workflow.id)
    if existing is not None and existing.agent_id!=agent_id:
        raise AppError("CONFLICT","Workflow is already associated with an Agent",409)
    previous=session.scalar(select(AgentWorkflowBinding).where(AgentWorkflowBinding.agent_id==agent_id))
    previous_pk=previous.workflow_pk if previous else None
    if previous is not None and previous.workflow_pk!=workflow.id:session.delete(previous);session.flush()
    if existing is None:session.add(AgentWorkflowBinding(workflow_pk=workflow.id,agent_id=agent_id,owner_id=context.operator_id,created_at=utc_now()))
    session.add(OwnershipAudit(id=new_id("audit"),actor_id=context.operator_id,action="claim_exclusive_agent_workflow",resource_kind="agent",resource_id=agent_id,details=json.dumps({"workflow_id":workflow_id,"previous_workflow_pk":previous_pk}),created_at=utc_now()))
    try:session.flush()
    except IntegrityError as exc:raise AppError("CONFLICT","Workflow binding changed concurrently",409) from exc
    return {"agent_id":agent_id,"workflow_id":workflow_id,"exclusive":True}
