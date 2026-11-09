"""Explicit external projections. Central must authorize before calling."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from app.errors import AppError
from .values import reject_credentials,canonical_payload_digest
from .observability import project_usage

STATUSES={'queued','running','resuming','paused','waiting_approval','outcome_unknown','succeeded','failed','cancelled','interrupted'}
PRIVATE={'request','workflow_snapshot','agent_snapshots','variable_pool','system_prompt','reasoning_content','reasoning','authorization','secret_ref','api_key','checkpoint','traceback'}


def public_outputs(value,schema):
    """Require declared fields/types, prohibit remote schema resolution and keys."""
    def safe_schema(item,depth=0):
        if depth>24:raise ValueError()
        if isinstance(item,dict):
            if any(k in item for k in ('$ref','$dynamicRef','$recursiveRef')):raise ValueError()
            for child in item.values():safe_schema(child,depth+1)
        elif isinstance(item,list):
            for child in item:safe_schema(child,depth+1)
    def fields(item,shape):
        if isinstance(item,dict):
            props=shape.get('properties',{})
            if any(k in PRIVATE or k not in props for k in item):raise ValueError()
            return {k:fields(v,props[k]) for k,v in item.items()}
        if isinstance(item,list):return [fields(v,shape.get('items',{})) for v in item]
        return item
    try:
        safe_schema(schema);Draft202012Validator.check_schema(schema)
        if not isinstance(schema,dict) or not schema.get('type'):raise ValueError()
        if any(Draft202012Validator(schema).iter_errors(value)):raise ValueError()
        projected=fields(value,schema);reject_credentials(projected)
        canonical_payload_digest(projected,maximum_bytes=131072,maximum_depth=24)
        return deepcopy(projected)
    except (ValueError,TypeError,AppError):raise AppError('APP_OUTPUT_INVALID','最终输出不符合应用公开字段契约',422) from None


def project_run(run,*,authority,outputs=None,usage=None,request_id=None,output_schema=None,replayed=False,timing=None):
    run_id=run.get('run_id',run.get('id'));status=run.get('status')
    if not isinstance(run_id,str) or status not in STATUSES:raise AppError('APP_OUTPUT_INVALID','运行状态无效',503)
    result={'request_id':request_id,'application_id':authority['application_id'],'version_id':authority['version_id'],
        'run_id':run_id,'status':status,'created_at':run.get('created_at'),'replayed':bool(replayed),
        'links':{k:f'/api/open/v1/runs/{run_id}{suffix}' for k,suffix in [('self',''),('events','/events'),('cancel','/cancel')]}}
    if status=='succeeded':
        if output_schema is None:raise AppError('APP_OUTPUT_INVALID','应用未提供公开输出契约',503)
        result['outputs']=public_outputs(outputs,output_schema)
        result['usage']=project_usage(usage)
    elif usage is not None:result['usage']=project_usage(usage)
    if timing is not None:result['timing']=deepcopy(timing)
    return result


def project_event(event,*,authority=None,outputs=None,usage=None,output_schema=None):
    sequence=event.get('sequence');status=event.get('status')
    if type(sequence) is not int or sequence<1 or status not in STATUSES:return None
    # Only lifecycle transitions; never forward raw event.data or node I/O.
    kind=event.get('type','')
    if kind not in {'run.queued','run.started','run.completed','run.failed','run.cancelled','run.paused','run.resuming','run.interrupted','effect.unknown',
                    'approval.requested','approval.decided','approval.expired','node.started','node.completed','node.failed',
                    'execution.step_started','execution.step_completed','execution.step_failed'}:return None
    scope='run' if kind.startswith('run.') else 'approval' if kind.startswith('approval.') else 'effect' if kind.startswith('effect.') else 'step'
    value={'sequence':sequence,'type':'progress','scope':scope,'status':status}
    if status=='succeeded' and kind=='run.completed':
        value['type']='result';value['outputs']=public_outputs(outputs,output_schema or {});value['usage']=project_usage(usage)
    from .public_schemas import ResultEvent,ProgressEvent
    model=ResultEvent if value['type']=='result' else ProgressEvent
    return model.model_validate(value).model_dump(mode='json',exclude_unset=True)


def project_trace(trace,*,authority):
    if 'trace.read' not in authority['scopes'] or not authority['runtime_policy'].get('expose_trace',False):
        raise AppError('APP_SCOPE_DENIED','此应用未开放安全运行摘要',403)
    # No node names, prompts, data, reasoning or provider metadata.
    return {'status':trace.get('status') if trace.get('status') in STATUSES else 'unknown',
        'steps':[{'status':s['status']} for s in trace.get('steps',[])[:256] if isinstance(s,dict) and s.get('status') in STATUSES]}
