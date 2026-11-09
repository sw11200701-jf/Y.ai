"""Additive schema4 migration. Existing SQLite is backed up before schema writes."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import inspect,text,select
from app.errors import AppError

USER_COLUMNS={
    "role_id":"VARCHAR(100)","permission_overrides":"TEXT","permission_ceiling":"TEXT",
    "created_by":"VARCHAR(100)","must_change_password":"BOOLEAN NOT NULL DEFAULT 0",
    "department_id":"VARCHAR(100)","company_id":"VARCHAR(100)",
}

def prepare(engine,data_dir):
    inspector=inspect(engine)
    if not inspector.has_table("users"):return None
    with engine.connect() as connection:
        if inspector.has_table("schema_versions") and connection.execute(text("SELECT 1 FROM schema_versions WHERE version=5")).first():return None
    if engine.dialect.name!="sqlite":raise AppError("RBAC_BACKUP_REQUIRED","此数据库的角色迁移需要先完成受控备份和部署验证",503)
    database=Path(engine.url.database).resolve()
    folder=Path(data_dir).resolve()/"backups";folder.mkdir(parents=True,exist_ok=True)
    backup=folder/("pre-access5-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".sqlite3")
    with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as source,closing(sqlite3.connect(backup)) as target:source.backup(target)
    columns={column["name"] for column in inspector.get_columns("users")}
    with engine.begin() as connection:
        for name,declaration in USER_COLUMNS.items():
            if name not in columns:connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {declaration}"))
    return str(backup)

def apply(session,backup_path):
    from app.db import SchemaVersion,OwnershipAudit,new_id,utc_now
    from app.auth_models import User,Installation
    from app.rbac import seed_roles
    if session.get(SchemaVersion,4):return
    users=list(session.scalars(select(User)))
    founder=None;basis="new_installation"
    if users:
        installation=session.get(Installation,1)
        if installation:
            founder=session.get(User,installation.admin_user_id);basis="installation.admin_user_id"
        else:
            actors=set(session.scalars(select(OwnershipAudit.actor_id).where(OwnershipAudit.action=="installation_claim_legacy")))
            if len(actors)==1:founder=session.get(User,next(iter(actors)));basis="unique_installation_claim_legacy_audit"
        if founder is None or not founder.active or founder.role not in ("admin","super_admin"):
            raise AppError("RBAC_MIGRATION_REVIEW_REQUIRED","无法确定有效的首位安装管理员，角色迁移已停止，请核对初始化记录与备份",503)
        # Never choose a different account from creation order alone or promote all admins.
        if any(user.role=="super_admin" and user.id!=founder.id for user in users):
            raise AppError("RBAC_MIGRATION_REVIEW_REQUIRED","现有超级管理员与初始化证据不一致，迁移已停止",503)
        founder.role="super_admin"
    scopes={(user.tenant_id,user.workspace_id) for user in users}
    for tenant_id,workspace_id in scopes:seed_roles(session,tenant_id,workspace_id)
    session.add(OwnershipAudit(id=new_id("audit"),actor_id=founder.id if founder else "system",action="schema4_rbac_migration",resource_kind="installation",resource_id="1",details=json.dumps({"basis":basis,"promoted_user_id":founder.id if founder else None,"backup_path":backup_path,"existing_passwords_changed":False}),created_at=utc_now()))
    session.add(SchemaVersion(version=4,applied_at=utc_now()));session.flush()

def apply_data(session):
    from app.db import SchemaVersion,utc_now
    from app.auth_models import User
    from app.data_models import DataPolicyState
    if session.get(SchemaVersion,5):return
    for tenant,workspace in set(session.execute(select(User.tenant_id,User.workspace_id))):
        if session.get(DataPolicyState,(tenant,workspace)) is None:session.add(DataPolicyState(tenant_id=tenant,workspace_id=workspace,revision=1))
    session.add(SchemaVersion(version=5,applied_at=utc_now()));session.flush()
