"""Identity tables share the engineering database metadata, never business payloads."""
from sqlalchemy import String,Text,Boolean,Integer,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.db import Base

class Installation(Base):
    __tablename__="installation"
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    admin_user_id: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50))

class AccessRole(Base):
    __tablename__="access_roles"
    __table_args__=(UniqueConstraint("tenant_id","workspace_id","key"),)
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    key: Mapped[str]=mapped_column(String(100))
    tenant_id: Mapped[str]=mapped_column(String(100))
    workspace_id: Mapped[str]=mapped_column(String(100))
    name: Mapped[str]=mapped_column(String(100))
    base_role: Mapped[str]=mapped_column(String(20))
    permissions: Mapped[str]=mapped_column(Text)
    builtin: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[str]=mapped_column(String(50))
    updated_at: Mapped[str]=mapped_column(String(50))

class User(Base):
    __tablename__="users"
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    username: Mapped[str]=mapped_column(String(100),unique=True,index=True)
    display_name: Mapped[str]=mapped_column(String(100))
    password_hash: Mapped[str]=mapped_column(Text)
    tenant_id: Mapped[str]=mapped_column(String(100))
    workspace_id: Mapped[str]=mapped_column(String(100))
    role: Mapped[str]=mapped_column(String(20))
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    allowed_entities: Mapped[str]=mapped_column(Text,default='[]')
    created_at: Mapped[str]=mapped_column(String(50))
    role_id: Mapped[str|None]=mapped_column(String(100),nullable=True)
    permission_overrides: Mapped[str|None]=mapped_column(Text,nullable=True)
    permission_ceiling: Mapped[str|None]=mapped_column(Text,nullable=True)
    created_by: Mapped[str|None]=mapped_column(String(100),nullable=True)
    must_change_password: Mapped[bool]=mapped_column(Boolean,default=False,server_default="0")
    department_id: Mapped[str|None]=mapped_column(String(100),nullable=True)
    company_id: Mapped[str|None]=mapped_column(String(100),nullable=True)

class AuthSession(Base):
    __tablename__="auth_sessions"
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    token_hash: Mapped[str]=mapped_column(String(64),unique=True,index=True)
    user_id: Mapped[str|None]=mapped_column(String(100),nullable=True,index=True)
    created_at: Mapped[str]=mapped_column(String(50))
    expires_at: Mapped[str]=mapped_column(String(50))
    last_seen_at: Mapped[str]=mapped_column(String(50))
    revoked: Mapped[bool]=mapped_column(Boolean,default=False)

class LoginLimit(Base):
    __tablename__="login_limits"
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    count: Mapped[int]=mapped_column(Integer,default=0)
    window_start: Mapped[str]=mapped_column(String(50))
    blocked_until: Mapped[str|None]=mapped_column(String(50),nullable=True)

class ResourceGrant(Base):
    __tablename__="resource_grants"
    __table_args__=(UniqueConstraint("resource_pk","user_id"),)
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    resource_pk: Mapped[int]=mapped_column(Integer,index=True)
    user_id: Mapped[str]=mapped_column(String(100),index=True)
    permission: Mapped[str]=mapped_column(String(20))

class RunAuthorization(Base):
    __tablename__="run_authorizations"
    run_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    user_id: Mapped[str]=mapped_column(String(100),index=True)
    session_id: Mapped[str]=mapped_column(String(100),index=True)

class AgentWorkflowBinding(Base):
    __tablename__="agent_workflow_bindings"
    workflow_pk: Mapped[int]=mapped_column(Integer,primary_key=True)
    agent_id: Mapped[str]=mapped_column(String(100),unique=True,index=True)
    owner_id: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50))
