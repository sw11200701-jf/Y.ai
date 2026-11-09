"""Same-actor status directory, separate from access to run contents."""
import json
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
