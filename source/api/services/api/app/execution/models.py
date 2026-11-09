"""Private execution state, never exposed through generic Resource routes."""
from sqlalchemy import String, Text, Boolean, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    workspace_id: Mapped[str] = mapped_column(String(100))
    principal_id: Mapped[str] = mapped_column(String(100))
    engine_key: Mapped[str] = mapped_column(String(200))
    dsl_hash: Mapped[str] = mapped_column(String(64))
    state_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    initial_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)


class ExecutionBlob(Base):
    __tablename__ = "execution_blobs"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(50))


class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    execution_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    node_key: Mapped[str] = mapped_column(String(500))
    input_ref: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    side_effect: Mapped[bool] = mapped_column(Boolean)
    result_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[str] = mapped_column(String(50))
    completed_at: Mapped[str | None] = mapped_column(String(50), nullable=True)


class ExecutionApproval(Base):
    __tablename__ = "execution_approvals"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    execution_id: Mapped[str] = mapped_column(String(100), unique=True)
    node_key: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(200))
    approver_ids: Mapped[str] = mapped_column(Text)
    allow_self: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    expires_at: Mapped[str] = mapped_column(String(50))
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    consumed_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
