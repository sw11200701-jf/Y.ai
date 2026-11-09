"""Real application service principals and partitioned execution authorization.

No row in these tables is a human User or AuthSession. Credential/key/quota
tables belong exclusively to external_api; this module never stores a token.
"""
from sqlalchemy import String,Text,Integer,Boolean,UniqueConstraint,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from app.db import Base


class ServicePrincipal(Base):
    __tablename__='service_principals'
    __table_args__=(UniqueConstraint('tenant_id','workspace_id','client_id'),)
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    tenant_id:Mapped[str]=mapped_column(String(100),index=True)
    workspace_id:Mapped[str]=mapped_column(String(100),index=True)
    application_id:Mapped[str]=mapped_column(String(100))
    client_id:Mapped[str]=mapped_column(String(100),unique=True)
    display_name:Mapped[str]=mapped_column(String(120))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    allowed_entities:Mapped[str]=mapped_column(Text,default='[]')
    department_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    company_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    created_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(50))
    updated_at:Mapped[str]=mapped_column(String(50))
    principal_type='service'
    role='service'
    must_change_password=False


class ServiceResourceGrant(Base):
    __tablename__='service_resource_grants'
    __table_args__=(UniqueConstraint('principal_id','version_id','resource_pk'),)
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    principal_id:Mapped[str]=mapped_column(ForeignKey('service_principals.id'),index=True)
    version_id:Mapped[str]=mapped_column(String(100),index=True)
    resource_pk:Mapped[int]=mapped_column(ForeignKey('resources.id'),index=True)
    actions:Mapped[str]=mapped_column(Text)
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    created_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(50))


class ServiceResourceBinding(Base):
    __tablename__='service_resource_bindings'
    resource_pk:Mapped[int]=mapped_column(ForeignKey('resources.id'),primary_key=True)
    principal_id:Mapped[str]=mapped_column(String(100),index=True)
    tenant_id:Mapped[str]=mapped_column(String(100))
    workspace_id:Mapped[str]=mapped_column(String(100))
    application_id:Mapped[str]=mapped_column(String(100))
    version_id:Mapped[str]=mapped_column(String(100))
    client_id:Mapped[str]=mapped_column(String(100),index=True)
    external_user_hash:Mapped[str]=mapped_column(String(64),index=True)
    created_by_key_id:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(50))


class MachineRunAuthorization(Base):
    __tablename__='machine_run_authorizations'
    run_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    principal_id:Mapped[str]=mapped_column(String(100),index=True)
    tenant_id:Mapped[str]=mapped_column(String(100))
    workspace_id:Mapped[str]=mapped_column(String(100))
    application_id:Mapped[str]=mapped_column(String(100))
    version_id:Mapped[str]=mapped_column(String(100))
    client_id:Mapped[str]=mapped_column(String(100),index=True)
    external_user_hash:Mapped[str]=mapped_column(String(64))
    admission_key_id:Mapped[str]=mapped_column(String(100))
    executing_key_id:Mapped[str]=mapped_column(String(100))
    release_sha256:Mapped[str]=mapped_column(String(64))
    reservation_id:Mapped[str|None]=mapped_column(String(100),nullable=True)
    created_at:Mapped[str]=mapped_column(String(50))


class ModuleMigration(Base):
    __tablename__='module_migrations'
    module:Mapped[str]=mapped_column(String(100),primary_key=True)
    version:Mapped[int]=mapped_column(Integer,primary_key=True)
    base_core:Mapped[str]=mapped_column(String(50))
    checksum:Mapped[str]=mapped_column(String(64))
    applied_at:Mapped[str]=mapped_column(String(50))


class MachineStepUsage(Base):
    __tablename__='machine_step_usage'
    execution_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    run_id:Mapped[str]=mapped_column(String(100),index=True)
    status:Mapped[str]=mapped_column(String(20),default='unknown')
    tokens:Mapped[str]=mapped_column(Text,default='{}')


CENTRAL_TABLES=frozenset({'service_principals','service_resource_grants','service_resource_bindings','machine_run_authorizations','module_migrations','machine_step_usage'})
