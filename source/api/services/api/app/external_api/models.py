"""Private application tables; explicit central module migration imports these.

No create_all, migration, commit, seed, Resource JSON or import from __init__.
The central feature gate must import/register this module only when enabled.
"""
from sqlalchemy import String, Text, Integer, LargeBinary, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Application(Base):
    __tablename__='external_api_apps'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    tenant_id: Mapped[str]=mapped_column(String(100),index=True)
    workspace_id: Mapped[str]=mapped_column(String(100),index=True)
    owner_id: Mapped[str]=mapped_column(String(100),index=True)
    name: Mapped[str]=mapped_column(String(100))
    kind: Mapped[str]=mapped_column(String(20))
    description: Mapped[str]=mapped_column(Text,default='')
    status: Mapped[str]=mapped_column(String(20),default='active')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    active_version_id: Mapped[str|None]=mapped_column(String(100),nullable=True)
    quotas: Mapped[str]=mapped_column(Text)
    created_at: Mapped[str]=mapped_column(String(50))
    updated_at: Mapped[str]=mapped_column(String(50))


class ApplicationShare(Base):
    __tablename__='external_api_shares'
    __table_args__=(UniqueConstraint('application_id','user_id'),)
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    application_id: Mapped[str]=mapped_column(String(100),index=True)
    user_id: Mapped[str]=mapped_column(String(100),index=True)
    permissions: Mapped[str]=mapped_column(Text)


class ApplicationVersion(Base):
    __tablename__='external_api_versions'
    __table_args__=(UniqueConstraint('application_id','sequence'),)
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    application_id: Mapped[str]=mapped_column(String(100),index=True)
    sequence: Mapped[int]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(20),default='published')
    source: Mapped[str]=mapped_column(Text)
    source_sha256: Mapped[str]=mapped_column(String(64))
    bundle_sha256: Mapped[str]=mapped_column(String(64))
    bundle: Mapped[str]=mapped_column(Text)
    published_by: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50))


class ApplicationClient(Base):
    __tablename__='external_api_clients'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    application_id: Mapped[str]=mapped_column(String(100),index=True)
    principal_id: Mapped[str]=mapped_column(String(100),unique=True)
    name: Mapped[str]=mapped_column(String(100))
    status: Mapped[str]=mapped_column(String(20),default='active')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    default_version_id: Mapped[str]=mapped_column(String(100))
    version_ids: Mapped[str]=mapped_column(Text)
    scopes: Mapped[str]=mapped_column(Text)
    external_user_mode: Mapped[str]=mapped_column(String(20))
    expires_at: Mapped[int|None]=mapped_column(Integer,nullable=True)
    quotas: Mapped[str]=mapped_column(Text)
    resource_constraints: Mapped[str]=mapped_column(Text)
    created_at: Mapped[str]=mapped_column(String(50))


class ApplicationCredential(Base):
    __tablename__='external_api_credentials'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    client_id: Mapped[str]=mapped_column(String(100),index=True)
    selector: Mapped[str]=mapped_column(String(16),unique=True)
    digest: Mapped[bytes]=mapped_column(LargeBinary(32))
    algorithm: Mapped[str]=mapped_column(String(20),default='sha256-v1')
    prefix: Mapped[str]=mapped_column(String(20))
    last4: Mapped[str]=mapped_column(String(4))
    name: Mapped[str]=mapped_column(String(100))
    status: Mapped[str]=mapped_column(String(20),default='active')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    default_version_id: Mapped[str]=mapped_column(String(100))
    version_ids: Mapped[str]=mapped_column(Text)
    scopes: Mapped[str]=mapped_column(Text)
    expires_at: Mapped[int|None]=mapped_column(Integer,nullable=True)
    quotas: Mapped[str]=mapped_column(Text)
    resource_constraints: Mapped[str]=mapped_column(Text)
    created_at: Mapped[str]=mapped_column(String(50))


class TransactionGate(Base):
    __tablename__='external_api_transaction_gate'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    revision: Mapped[int]=mapped_column(Integer,default=0)


class IdempotencyClaim(Base):
    __tablename__='external_api_idempotency'
    namespace_digest: Mapped[str]=mapped_column(String(64),primary_key=True)
    token_digest: Mapped[str]=mapped_column(String(64),primary_key=True)
    body_hash: Mapped[str]=mapped_column(String(64))
    run_id: Mapped[str]=mapped_column(String(100),unique=True)
    reservation_id: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50))


class InvocationReservation(Base):
    __tablename__='external_api_reservations'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    run_id: Mapped[str]=mapped_column(String(100),unique=True)
    application_id: Mapped[str]=mapped_column(String(100),index=True)
    client_id: Mapped[str]=mapped_column(String(100),index=True)
    key_id: Mapped[str]=mapped_column(String(100),index=True)
    version_id: Mapped[str]=mapped_column(String(100))
    principal_id: Mapped[str]=mapped_column(String(100))
    external_user_hash: Mapped[str]=mapped_column(String(64))
    day: Mapped[int]=mapped_column(Integer)
    reserved_tokens: Mapped[int]=mapped_column(Integer)
    slot_active: Mapped[int]=mapped_column(Integer,default=1)
    settled: Mapped[int]=mapped_column(Integer,default=0)
    status: Mapped[str]=mapped_column(String(30),default='reserved')
    created_at: Mapped[str]=mapped_column(String(50))


class QuotaBucket(Base):
    __tablename__='external_api_quota_buckets'
    scope: Mapped[str]=mapped_column(String(150),primary_key=True)
    window: Mapped[str]=mapped_column(String(50),primary_key=True)
    requests: Mapped[int]=mapped_column(Integer,default=0)
    invocations: Mapped[int]=mapped_column(Integer,default=0)
    slots: Mapped[int]=mapped_column(Integer,default=0)
    reserved_tokens: Mapped[int]=mapped_column(Integer,default=0)
    actual_tokens: Mapped[int]=mapped_column(Integer,default=0)


class InvocationUsage(Base):
    __tablename__='external_api_usage'
    run_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    status: Mapped[str]=mapped_column(String(20))
    tokens: Mapped[str]=mapped_column(Text)
    reserved_tokens: Mapped[int]=mapped_column(Integer)
    settled_at: Mapped[str]=mapped_column(String(50))


class Conversation(Base):
    __tablename__='external_api_conversations'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    partition: Mapped[str]=mapped_column(String(64),index=True)
    revision: Mapped[int]=mapped_column(Integer,default=0)
    active_run_id: Mapped[str|None]=mapped_column(String(100),nullable=True)
    created_at: Mapped[str]=mapped_column(String(50))


class ConversationMessage(Base):
    __tablename__='external_api_messages'
    __table_args__=(UniqueConstraint('conversation_id','sequence'),UniqueConstraint('run_id','role'))
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    conversation_id: Mapped[str]=mapped_column(String(100),index=True)
    run_id: Mapped[str]=mapped_column(String(100),index=True)
    sequence: Mapped[int]=mapped_column(Integer)
    role: Mapped[str]=mapped_column(String(20))
    content: Mapped[str]=mapped_column(Text)
    created_at: Mapped[str]=mapped_column(String(50))


class ApplicationFile(Base):
    __tablename__='external_api_files'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    partition: Mapped[str]=mapped_column(String(64),index=True)
    resource_kind: Mapped[str]=mapped_column(String(50))
    resource_id: Mapped[str]=mapped_column(String(100))
    filename: Mapped[str]=mapped_column(String(150))
    media_type: Mapped[str]=mapped_column(String(100))
    size_bytes: Mapped[int]=mapped_column(Integer)
    sha256: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[str]=mapped_column(String(50))


class CallRecord(Base):
    __tablename__='external_api_calls'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    request_id: Mapped[str]=mapped_column(String(100))
    run_id: Mapped[str]=mapped_column(String(100),unique=True)
    application_id: Mapped[str]=mapped_column(String(100),index=True)
    client_id: Mapped[str]=mapped_column(String(100),index=True)
    key_id: Mapped[str]=mapped_column(String(100))
    key_prefix: Mapped[str]=mapped_column(String(20))
    version_id: Mapped[str]=mapped_column(String(100))
    status: Mapped[str]=mapped_column(String(30))
    safe_code: Mapped[str|None]=mapped_column(String(80),nullable=True)
    usage_status: Mapped[str]=mapped_column(String(20),default='reserved')
    created_at: Mapped[str]=mapped_column(String(50))
    updated_at: Mapped[str]=mapped_column(String(50))


class StreamLease(Base):
    __tablename__='external_api_stream_leases'
    id: Mapped[str]=mapped_column(String(100),primary_key=True)
    client_id: Mapped[str]=mapped_column(String(100),index=True)
    key_id: Mapped[str]=mapped_column(String(100),index=True)
    run_id: Mapped[str]=mapped_column(String(100))
    partition: Mapped[str]=mapped_column(String(64))
    expires_at: Mapped[int]=mapped_column(Integer,index=True)


class CallTiming(Base):
    __tablename__='external_api_call_timing'
    run_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    admitted_at: Mapped[str]=mapped_column(String(50))
    finished_at: Mapped[str|None]=mapped_column(String(50),nullable=True)
    elapsed_ms: Mapped[int|None]=mapped_column(Integer,nullable=True)
    timing_status: Mapped[str]=mapped_column(String(20),default='pending')
    usage_source: Mapped[str]=mapped_column(String(30),default='pending')


class KeyActivity(Base):
    __tablename__='external_api_key_activity'
    key_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    last_admitted_at: Mapped[str]=mapped_column(String(50))


TABLES=tuple(cls.__table__ for cls in (Application,ApplicationShare,ApplicationVersion,ApplicationClient,ApplicationCredential,
    TransactionGate,IdempotencyClaim,InvocationReservation,QuotaBucket,InvocationUsage,Conversation,ConversationMessage,ApplicationFile,CallRecord,StreamLease,CallTiming,KeyActivity))
