"""Short caller-owned SQLite transactions; no commits and no process locks."""
import json
import time
from datetime import datetime, timezone
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from app.errors import AppError
from .models import TransactionGate


def dump(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True)
def load(value):return json.loads(value)
def epoch():return int(time.time())
def iso(value):return datetime.fromtimestamp(value,timezone.utc).isoformat() if value is not None else None


def expiry(value):
    if value is None:return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        stamp=int(parsed.timestamp())
    except (ValueError,TypeError,OverflowError):raise AppError('APP_INPUT_INVALID','到期时间须为带时区的有效时间',422) from None
    if stamp<=epoch():raise AppError('APP_INPUT_INVALID','到期时间必须晚于当前时间',422)
    return stamp


def write_gate(session):
    """Take DB writer lock before read/modify/write. Held until caller ends txn.

    Exactly one same-DB writer across processes on supported SQLite. Central
    submit holds this transaction through execution/auth/ledger inserts, commits,
    then enqueues. If caller already read in an explicit deferred transaction,
    SQLITE_BUSY_SNAPSHOT must cause a whole transaction retry, never partial retry.
    """
    if session.get_bind().dialect.name!='sqlite':
        raise AppError('APP_STORAGE_UNSUPPORTED','应用API当前仅支持已验证的SQLite事务',503)
    session.execute(insert(TransactionGate).values(id=1,revision=0).on_conflict_do_nothing(index_elements=['id']))
    session.execute(update(TransactionGate).where(TransactionGate.id==1).values(revision=TransactionGate.revision+1))


def fresh(session,model,identifier):return session.get(model,identifier,populate_existing=True)


def conflict():raise AppError('APP_REVISION_CONFLICT','记录已变化，请刷新后重试',409)


def minimum_quotas(*values):
    return {name:min(v[name] for v in values) for name in values[0]}


def constraints_intersection(one,two):
    resources=[]
    for item in one.get('resources',[]):
        for other in two.get('resources',[]):
            if (item['kind'],item['resource_id'])!=(other['kind'],other['resource_id']):continue
            a,b=item.get('version_id'),other.get('version_id')
            if a and b and a!=b:continue
            actions=sorted(set(item['actions'])&set(other['actions']))
            if actions:resources.append({**item,'version_id':a or b,'actions':actions})
    return {'resources':resources,'allowed_entities':sorted(set(one.get('allowed_entities',[]))&set(two.get('allowed_entities',[])))}


def require_subset(candidate,parent):
    # Strict equality after normalization also prevents omitted version widening.
    actual=constraints_intersection(candidate,parent)
    def normalized(value):
        return {'resources':sorted([{'kind':x['kind'],'resource_id':x['resource_id'],'version_id':x.get('version_id'),'actions':sorted(set(x['actions']))} for x in value['resources']],key=dump),
                'allowed_entities':sorted(set(value['allowed_entities']))}
    if normalized(candidate)!=normalized(actual):raise AppError('APP_SCOPE_DENIED','授权范围不能超出上级许可',403)
