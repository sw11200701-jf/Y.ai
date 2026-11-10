"""Same-actor status directory, separate from access to run contents."""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import exists, func
from app.execution.contracts import TERMINAL_STATUSES
from app.execution.models import ExecutionStep
from app.execution.retained_intents import is_retained
from app.settings import settings
from app.db import Resource,_resource_query

def directory(session,context):
    items=[]
    for row in session.scalars(_resource_query(session,'run',None,context).order_by(Resource.id.desc())):
        # A grant to a run cannot reveal another actor's private execution.
        if row.owner_id!=context.operator_id:continue
        run=json.loads(row.payload)
        # Listing must not parse every old workbook/checkpoint or disappear
        # after model/source revisions. The detail route checks current full
        # dependencies separately. No prompt/error/output/source/filename or
        # attachment metadata is exposed through this status-only index.
        items.append({k:run[k] for k in ('id','status','mode','created_at','updated_at') if k in run}|
            {'directory_entry':True,'content_access':'checked_on_open',
             'capabilities':{'can_resume':False,'can_cancel':False},'artifact_ids':[]})
    return items

def retention_summary(session, context):
    """Count only the caller's aged records; this is a read-only policy report."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=settings.run_retention_days)).isoformat()
    status = func.json_extract(Resource.payload, "$.status")
    updated = func.coalesce(func.json_extract(Resource.payload, "$.updated_at"),
                            func.json_extract(Resource.payload, "$.created_at"))
    issued = exists().where(ExecutionStep.run_id == Resource.resource_id,
                            ExecutionStep.side_effect.is_(True), ExecutionStep.status == "issued")
    rows = _resource_query(session, "run", None, context).with_only_columns(Resource.resource_id, status, issued)
    rows = rows.where(Resource.owner_id == context.operator_id, updated <= cutoff)
    completed, protected = 0, 0
    for identifier, state, unresolved in session.execute(rows):
        if state in TERMINAL_STATUSES and not unresolved and not is_retained(identifier):
            completed += 1
        else:
            protected += 1
    return {"retention_days": settings.run_retention_days, "cutoff": cutoff,
            "completed_over_age": completed, "protected_over_age": protected}
