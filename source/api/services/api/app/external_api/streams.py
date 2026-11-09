"""Durable bounded stream leases. Heartbeats never count as new HTTP calls."""
from sqlalchemy import select,func,delete
from app.db import new_id
from app.errors import AppError
from . import bridge
from .models import StreamLease
from .storage import write_gate,fresh,epoch


def acquire(session,*,authority,run_id):
    write_gate(session);authority=bridge.current(session,authority,'events.read');now=epoch()
    session.execute(delete(StreamLease).where(StreamLease.expires_at<=now))
    count=session.scalar(select(func.count()).select_from(StreamLease).where(StreamLease.client_id==authority['client_id'],StreamLease.expires_at>now))
    if count>=authority['runtime_policy'].get('max_streams',2):
        raise AppError('APP_QUOTA_EXCEEDED','应用事件连接数已达到上限',429,{'retry_after':1})
    row=StreamLease(id=new_id('appstream'),client_id=authority['client_id'],key_id=authority['key_id'],run_id=run_id,
        partition=bridge.partition(authority),expires_at=now+authority['runtime_policy'].get('stream_lease_seconds',30))
    session.add(row);session.flush();return {'lease_id':row.id,'expires_at':row.expires_at}


def renew(session,*,authority,lease_id):
    write_gate(session);authority=bridge.current(session,authority,'events.read');row=fresh(session,StreamLease,lease_id)
    if row is None or row.expires_at<=epoch() or row.key_id!=authority['key_id'] or row.partition!=bridge.partition(authority):
        raise AppError('APP_STREAM_EXPIRED','事件连接租约已失效，请重新连接',409)
    row.expires_at=epoch()+authority['runtime_policy'].get('stream_lease_seconds',30);session.flush()
    return row.expires_at


def release(session,*,lease_id):
    # Internal finally cleanup is safe even after revocation; never yields data.
    write_gate(session);session.execute(delete(StreamLease).where(StreamLease.id==lease_id));session.flush()
