"""Frozen central facade. Caller owns transaction, execution and current ACLs.

No User/session impersonation, scheduler, network, implicit commit or token cache.
"""
import hashlib
import re
from sqlalchemy import select
from app.db import new_id, utc_now
from app.errors import AppError
from .keys import KeyHash, lookup_selector, verify_key
from .models import (Application,ApplicationClient,ApplicationCredential,ApplicationVersion,IdempotencyClaim,
    InvocationReservation,QuotaBucket,InvocationUsage,CallRecord,CallTiming,KeyActivity)
from .schemas import RuntimeAuthority,ReleaseBundleV1,RuntimePolicy,Quotas
from .storage import fresh,load,dump,epoch,write_gate,constraints_intersection
from .values import canonical_payload_digest,external_user_label,bounded_reported_usage


def _invalid():raise AppError('APP_KEY_INVALID','应用凭据无效或已失效',401)


def _rows(session,key_id):
    key=fresh(session,ApplicationCredential,key_id)
    client=fresh(session,ApplicationClient,key.client_id) if key else None
    app=fresh(session,Application,client.application_id) if client else None
    now=epoch()
    if key is None or client is None or app is None or key.status!='active' or client.status!='active':_invalid()
    if any(v is not None and v<=now for v in [key.expires_at,client.expires_at]):_invalid()
    if app.status!='active':raise AppError('APP_DISABLED','应用当前不可调用',403)
    return key,client,app


def verify_bearer(session,*,token,requested_version_id=None,external_user=None):
    selector=lookup_selector(token)
    key=session.scalar(select(ApplicationCredential).where(ApplicationCredential.selector==selector).execution_options(populate_existing=True)) if selector else None
    expected=KeyHash(key.id,key.selector,key.digest,key.last4,key.algorithm) if key else None
    if not verify_key(token,expected):_invalid()
    key,client,_=_rows(session,key.id)
    if external_user is None:
        if client.external_user_mode=='required':raise AppError('APP_INPUT_INVALID','此客户端要求提供外部用户标识',422)
        label={'mode':'client','client_id':client.id}
    else:label={'mode':'user','client_id':client.id,'user':external_user_label(external_user)}
    return authority_by_id(session,key_id=key.id,client_id=client.id,application_id=client.application_id,
        version_id=requested_version_id or key.default_version_id,external_user_hash=canonical_payload_digest(label))


def authority_by_id(session,*,key_id,client_id,application_id,version_id,external_user_hash):
    key,client,app=_rows(session,key_id)
    if (client.id,app.id)!=(client_id,application_id):_invalid()
    if not isinstance(external_user_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',external_user_hash):_invalid()
    version=fresh(session,ApplicationVersion,version_id)
    if version_id not in set(load(key.version_ids))&set(load(client.version_ids)) or version is None or version.application_id!=app.id or version.status!='published':
        raise AppError('APP_VERSION_UNAVAILABLE','指定应用版本不可调用',403)
    bundle=_bundle(version)
    return RuntimeAuthority(key_id=key.id,client_id=client.id,principal_id=client.principal_id,application_id=app.id,version_id=version.id,
        tenant_id=app.tenant_id,workspace_id=app.workspace_id,scopes=sorted(set(load(key.scopes))&set(load(client.scopes))),external_user_hash=external_user_hash,
        resource_constraints=constraints_intersection(load(key.resource_constraints),load(client.resource_constraints)),
        runtime_policy=bundle['runtime_policy'],credential_revision=key.revision).model_dump(mode='json')


def current(session,authority,scope=None):
    value=authority.model_dump(mode='json') if hasattr(authority,'model_dump') else authority
    expected=RuntimeAuthority.model_validate(value)
    result=authority_by_id(session,key_id=expected.key_id,client_id=expected.client_id,application_id=expected.application_id,
        version_id=expected.version_id,external_user_hash=expected.external_user_hash)
    if (result['principal_id'],result['tenant_id'],result['workspace_id'])!=(expected.principal_id,expected.tenant_id,expected.workspace_id):_invalid()
    if scope and scope not in result['scopes']:raise AppError('APP_SCOPE_DENIED','此应用Key没有所需调用能力',403)
    return result


def _bundle(version):
    try:
        bundle=load(version.bundle)
        parsed=ReleaseBundleV1.model_validate(bundle).model_dump(mode='json')
        valid=canonical_payload_digest(bundle,maximum_bytes=1048576,maximum_depth=32)==version.bundle_sha256
    except (ValueError,TypeError,AppError):
        raise AppError('APP_DEPENDENCY_CHANGED','应用发布快照校验失败',409) from None
    if not valid:raise AppError('APP_DEPENDENCY_CHANGED','应用发布快照校验失败',409)
    return parsed


def load_release(session,*,authority):
    authority=current(session,authority)
    return _bundle(fresh(session,ApplicationVersion,authority['version_id']))


def partition(authority):
    return canonical_payload_digest({k:authority[k] for k in ('tenant_id','workspace_id','application_id','version_id','client_id','principal_id','external_user_hash')})


def _limits(session,authority):
    key,client,app=_rows(session,authority['key_id'])
    return [(f'app:{app.id}',Quotas.model_validate(load(app.quotas)).model_dump()),
            (f'client:{client.id}',Quotas.model_validate(load(client.quotas)).model_dump()),
            (f'key:{key.id}',Quotas.model_validate(load(key.quotas)).model_dump())]


def _bucket(session,scope,window):
    value=fresh(session,QuotaBucket,(scope,window))
    if value is None:
        value=QuotaBucket(scope=scope,window=window,requests=0,invocations=0,slots=0,reserved_tokens=0,actual_tokens=0)
        session.add(value);session.flush()
    return value


def _exceeded(retry=60):raise AppError('APP_QUOTA_EXCEEDED','应用调用已达到配置限额，请稍后重试',429,{'retry_after':max(1,int(retry))})


def check_http_quota(session,*,authority,operation):
    write_gate(session);authority=current(session,authority)
    now=epoch();values=[]
    for scope,limit in _limits(session,authority):
        bucket=_bucket(session,scope,f'm:{now//60}')
        if bucket.requests>=limit['requests_per_minute']:_exceeded(60-now%60)
        values.append(bucket)
    for bucket in values:bucket.requests+=1
    session.flush()


def reserve_invocation(session,*,authority,operation,idempotency_key,body_hash,proposed_run_id,request_id):
    write_gate(session);authority=current(session,authority,operation)
    if operation not in {'workflow.invoke','chat.invoke'}:raise AppError('APP_SCOPE_DENIED','调用操作无效',403)
    if not isinstance(idempotency_key,str) or not re.fullmatch(r'[\x21-\x7e]{8,128}',idempotency_key):
        raise AppError('APP_INPUT_INVALID','调用必须提供8至128个可见ASCII字符的幂等标识',422)
    if not isinstance(body_hash,str) or not re.fullmatch('[a-f0-9]{64}',body_hash):raise AppError('APP_INPUT_INVALID','请求摘要无效',422)
    namespace=canonical_payload_digest({'partition':partition(authority),'operation':operation})
    token_digest=hashlib.sha256(idempotency_key.encode('ascii')).hexdigest()
    claim=fresh(session,IdempotencyClaim,(namespace,token_digest))
    if claim:
        if claim.body_hash!=body_hash:raise AppError('APP_IDEMPOTENCY_CONFLICT','幂等标识已用于不同的请求内容',409)
        return {'replayed':True,'run_id':claim.run_id,'reservation_id':claim.reservation_id}
    day=epoch()//86400;tokens=RuntimePolicy.model_validate(authority['runtime_policy']).token_reservation
    values=[]
    for scope,limit in _limits(session,authority):
        daily=_bucket(session,scope,f'd:{day}');active=_bucket(session,scope,'active')
        if active.slots>=limit['concurrent_runs']:_exceeded(1)
        if daily.invocations>=limit['daily_invocations'] or daily.reserved_tokens+daily.actual_tokens+tokens>limit['daily_tokens']:_exceeded(86400-epoch()%86400)
        values.append((daily,active))
    for daily,active in values:daily.invocations+=1;daily.reserved_tokens+=tokens;active.slots+=1
    reservation_id=new_id('appreserve');now=utc_now()
    session.add(IdempotencyClaim(namespace_digest=namespace,token_digest=token_digest,body_hash=body_hash,run_id=proposed_run_id,reservation_id=reservation_id,created_at=now))
    session.add(InvocationReservation(id=reservation_id,run_id=proposed_run_id,application_id=authority['application_id'],client_id=authority['client_id'],key_id=authority['key_id'],
        version_id=authority['version_id'],principal_id=authority['principal_id'],external_user_hash=authority['external_user_hash'],day=day,reserved_tokens=tokens,
        slot_active=1,settled=0,status='reserved',created_at=now))
    key=fresh(session,ApplicationCredential,authority['key_id'])
    activity=fresh(session,KeyActivity,key.id)
    if activity is None:session.add(KeyActivity(key_id=key.id,last_admitted_at=now))
    else:activity.last_admitted_at=now
    session.add(CallTiming(run_id=proposed_run_id,admitted_at=now,timing_status='pending',usage_source='pending'))
    session.add(CallRecord(id=new_id('appcall'),request_id=request_id,run_id=proposed_run_id,application_id=authority['application_id'],client_id=authority['client_id'],
        key_id=authority['key_id'],key_prefix=key.prefix,version_id=authority['version_id'],status='queued',usage_status='reserved',created_at=now,updated_at=now))
    session.flush()
    return {'replayed':False,'run_id':proposed_run_id,'reservation_id':reservation_id}


def settle_invocation(session,*,run_id,status,usage,usage_unknown):
    write_gate(session)
    row=session.scalar(select(InvocationReservation).where(InvocationReservation.run_id==run_id).execution_options(populate_existing=True))
    if row is None or row.settled:return
    record=session.scalar(select(CallRecord).where(CallRecord.run_id==run_id))
    timing=fresh(session,CallTiming,run_id)
    from .projection import STATUSES
    if status not in STATUSES:return
    row.status=status
    if record:record.status=status;record.updated_at=utc_now()
    # An unresolved effect is not a completed reservation. Do not release its slot.
    if status=='outcome_unknown':
        if record:record.usage_status='unknown'
        session.flush();return
    if status not in {'succeeded','failed','cancelled'}:
        session.flush();return
    reported=bounded_reported_usage(usage)
    known=not usage_unknown and reported['status']=='known'
    if usage_unknown and reported['status']=='known':reported['status']='partial'
    actual=reported['tokens'].get('total_tokens',0) if known else None
    source=usage.get('source') if isinstance(usage,dict) else None
    from .observability import project_usage,finish_timing
    public=project_usage(reported,reserved_tokens=0 if known else row.reserved_tokens,source=source)
    for scope in [f'app:{row.application_id}',f'client:{row.client_id}',f'key:{row.key_id}']:
        active=_bucket(session,scope,'active');daily=_bucket(session,scope,f'd:{row.day}')
        if active.slots<1:raise AppError('APP_LEDGER_INVALID','应用预约账本不一致',503)
        active.slots-=1
        if known:daily.reserved_tokens-=row.reserved_tokens;daily.actual_tokens+=actual
    row.slot_active=0;row.settled=1;row.status=status
    finished=utc_now()
    session.add(InvocationUsage(run_id=run_id,status='actual' if known else reported['status'],tokens=dump(public['tokens']),reserved_tokens=0 if known else row.reserved_tokens,settled_at=finished))
    if timing:
        timing.usage_source=public['source'];finish_timing(timing,finished)
    if record:record.usage_status=public['status'];record.updated_at=finished
    session.flush()


# Projection is an allowlist, not an authorization shortcut.
from .projection import project_run,project_event,project_trace
