"""Versioned additive application modules over core6, excluding schema7/8/PG."""
import hashlib,json,sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import inspect,text
from app.errors import AppError

CORE_TABLES=frozenset({'schema_versions','resources','run_events','ownership_audit','installation','access_roles','users','auth_sessions','login_limits','resource_grants','run_authorizations','agent_workflow_bindings','data_grants','data_resource_links','data_policy_states','data_derivations','user_role_memberships','data_resource_aliases','data_table_bindings','execution_runs','execution_blobs','execution_steps','execution_approvals'})


def module_tables(metadata):
    from app.machine_models import CENTRAL_TABLES
    from app.external_api.models import TABLES
    approved=set(CENTRAL_TABLES)|{table.name for table in TABLES}
    extra=set(metadata.tables)-CORE_TABLES
    if extra!=approved:
        raise AppError('APP_API_BASE_UNSUPPORTED','发现未放行的数据模型，不能混合迁移',503)
    return [metadata.tables[name] for name in sorted(extra)]


def checksum(tables):
    values=[]
    for table in sorted(tables,key=lambda item:item.name):
        values.append({'table':table.name,'columns':[(c.name,str(c.type),c.nullable,c.primary_key) for c in table.columns],
            'unique':sorted([sorted(column.name for column in item.columns) for item in table.constraints if item.__class__.__name__=='UniqueConstraint']),
            'indexes':sorted([{'name':i.name,'unique':i.unique,'columns':[c.name for c in i.columns]} for i in table.indexes],key=lambda i:i['name'])})
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


V1_CHECKSUM='5402568304c05b2ba670855fd6cf04f9548e59ef398f01eb7aff97c4416fe2d0'
V2_TABLES=frozenset({'external_api_call_timing','external_api_key_activity'})


def migration_plan(tables):
    base=[table for table in tables if table.name not in V2_TABLES]
    added=[table for table in tables if table.name in V2_TABLES]
    if checksum(base)!=V1_CHECKSUM or {table.name for table in added}!=V2_TABLES:
        raise AppError('APP_API_MIGRATION_CHANGED','已冻结的应用模块1定义变化或模块2表不完整',503)
    return [(1,base,V1_CHECKSUM),(2,added,checksum(added))]


def _applied(connection,inspector,plan):
    if not inspector.has_table('module_migrations'):return set()
    rows=list(connection.execute(text("SELECT version,checksum,base_core FROM module_migrations WHERE module='application_api' ORDER BY version")))
    expected={version:(digest,'core6') for version,_,digest in plan}
    applied=set()
    for version,digest,core in rows:
        if version not in expected or expected[version]!=(digest,core):
            raise AppError('APP_API_MIGRATION_CHANGED','已应用模块定义不一致或版本尚未验证',503)
        applied.add(version)
    if applied and applied!=set(range(1,max(applied)+1)):
        raise AppError('APP_API_MIGRATION_INCOMPLETE','应用模块版本链不完整',503)
    for version,tables,_ in plan:
        for table in tables:
            present=inspector.has_table(table.name)
            if version in applied and not present:
                raise AppError('APP_API_MIGRATION_INCOMPLETE','已应用模块缺少表',503)
            if version not in applied and present:
                raise AppError('APP_API_MIGRATION_INCOMPLETE','发现无迁移凭据的应用表',503)
    return applied


def prepare(engine,data_dir,tables):
    if engine.dialect.name!='sqlite':raise AppError('APP_API_MIGRATION_REVIEW_REQUIRED','对外应用迁移目前仅验证SQLite，其他数据库需先评审',503)
    inspector=inspect(engine);plan=migration_plan(tables)
    if not inspector.has_table('resources'):return None
    if not inspector.has_table('schema_versions'):raise AppError('APP_API_BASE_UNSUPPORTED','对外应用需要已验证的core6基线',503)
    with engine.connect() as connection:
        versions=list(connection.execute(text('SELECT version FROM schema_versions ORDER BY version')).scalars())
        if versions!=[1,2,3,4,5,6]:raise AppError('APP_API_BASE_UNSUPPORTED','只允许从准确core6启用应用API；不混入Schema7/8开发迁移',503)
        applied=_applied(connection,inspector,plan)
        if applied=={version for version,_,_ in plan}:return None
    folder=Path(data_dir)/'backups';folder.mkdir(parents=True,exist_ok=True)
    destination=folder/('pre-application-api1-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
    source=Path(engine.url.database).resolve()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as origin,closing(sqlite3.connect(destination)) as target:origin.backup(target)
    return str(destination)


def apply(engine,metadata,tables):
    plan=migration_plan(tables)
    with engine.begin() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        applied=_applied(connection,inspect(connection),plan)
        for version,selected,digest in plan:
            if version in applied:continue
            metadata.create_all(connection,tables=selected)
            connection.execute(text("INSERT INTO module_migrations(module,version,base_core,checksum,applied_at) VALUES('application_api',:version,'core6',:checksum,:applied_at)"),
                {'version':version,'checksum':digest,'applied_at':datetime.now(timezone.utc).isoformat()})
