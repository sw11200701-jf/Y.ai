"""Atomic human workflow saves; content revision is independent of debug metadata."""
from copy import deepcopy
import hashlib
from pydantic import BaseModel,ConfigDict,Field
from app.contracts import WorkflowDefinition,WorkflowNode,WorkflowEdge
from app.db import SessionLocal,get_resource,save_resource,new_id
from app.errors import AppError
from app.execution.storage import encode
from app.runner.validation import validate_workflow

TRANSPORT={'graph_sha256','expected_graph_sha256','publication','access','metadata_only'}
def clean(value):return {k:deepcopy(v) for k,v in value.items() if k not in TRANSPORT}
def revision(value):return hashlib.sha256(encode(clean(value)).encode()).hexdigest()
def view(value):return clean(value)|{'graph_sha256':revision(value)}

class NodeSettingsSave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_graph_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    node_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    scope_path:list[str]=Field(default_factory=list,max_length=3)
    node:WorkflowNode
    input_schema:dict|None=None
    structure:WorkflowDefinition|None=None
    outgoing_edges:list[WorkflowEdge]|None=Field(default=None,max_length=17)

def persist(context,*,workflow_id=None,body=None,node_settings=None,expected=None,draft_node_id=None,draft_scope=()):
    from app.auth import validate_context
    from app.rbac import require_any_permission,audit
    context=context.model_copy(deep=True)
    with SessionLocal() as session:
        # Lock before authority/read: no stale snapshot can overwrite a sibling
        # or an ACL change between the read and revision comparison.
        if session.bind.dialect.name!='sqlite':raise AppError('WORKFLOW_STORAGE_UNSUPPORTED','此保存事务尚未验证当前数据库',503)
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        validate_context(context,session);require_any_permission(context,session,('page.ai',))
        if workflow_id is None:
            payload=clean(body.model_dump(mode='json'));payload['id']=new_id('workflow')
        else:
            current=get_resource(session,'workflow',workflow_id,context,access='write')
            if expected is None:raise AppError('WORKFLOW_REVISION_REQUIRED','请重新读取流程后再保存',428)
            if expected!=revision(current):raise AppError('WORKFLOW_REVISION_CONFLICT','流程已被其他保存更新，请合并后重试',409)
            if node_settings is None:payload=clean(body.model_dump(mode='json'));payload['id']=workflow_id
            else:
                payload=clean(node_settings.structure.model_dump(mode="json")) if node_settings.structure is not None else clean(current);payload["id"]=workflow_id;scope=payload
                for identifier in node_settings.scope_path:
                    parent=next((node for node in scope['nodes'] if node['id']==identifier),None)
                    if parent is None or parent['type'] not in {'iteration','loop'}:raise AppError('INVALID_NODE_SCOPE','节点作用域已变化',409)
                    scope=parent['config']['body']
                target=next((node for node in scope['nodes'] if node['id']==node_settings.node_id),None)
                if target is None:raise AppError('NODE_NOT_FOUND','待保存节点已不存在',409)
                replacement=node_settings.node.model_dump(mode='json')
                if replacement['id']!=target['id']:raise AppError('INVALID_NODE_ID','保存设置不能更改节点标识',422)
                if node_settings.input_schema is not None:
                    if target['type'] not in {'start','input','trigger_text','trigger_image','trigger_audio','trigger_file','trigger_mixed'} or replacement['type']!=target['type']:raise AppError('INVALID_INPUT_SCHEMA_TARGET','输入定义只能随开始节点原子保存',422)
                    scope['input_schema']=deepcopy(node_settings.input_schema)
                if node_settings.outgoing_edges is not None:
                    if target['type']!='question_classifier' or replacement['type']!=target['type']:
                        raise AppError('INVALID_CLASSIFIER_EDGE_TARGET','仅问题分类节点可关联保存本节点的类别出口',422)
                    outgoing=[edge.model_dump(mode='json') for edge in node_settings.outgoing_edges]
                    if any(edge['source']!=target['id'] for edge in outgoing):
                        raise AppError('INVALID_CLASSIFIER_EDGE_SOURCE','只能修改当前分类节点的出口，不能夹带其他连线',422)
                    scope['edges']=[edge for edge in scope['edges'] if edge['source']!=target['id']]+outgoing
                scope['nodes']=[replacement if node['id']==target['id'] else node for node in scope['nodes']]
        if workflow_id is not None:
            from app.execution.workflow_draft import guard_schema_changes
            guard_schema_changes(current,payload)
        validated=WorkflowDefinition.model_validate(payload)
        from app.execution.workflow_draft import validate_draft
        if node_settings is not None or draft_node_id is not None or getattr(validated,'ui',{}).get('field_contract')=='business_v1':
            validate_draft(validated,node_settings.node_id if node_settings else draft_node_id,node_settings.scope_path if node_settings else draft_scope)
            payload['ui']={**payload.get('ui',{}),'draft':True}
            validated=WorkflowDefinition.model_validate(payload)
        else:validate_workflow(validated)
        from app.user_variables import validate_references
        validate_references(validated,context,session);payload=clean(validated.model_dump(mode='json'))
        save_resource(session,'workflow',payload['id'],payload,context)
        audit(session,context,'workflow_node_settings_save' if node_settings else 'workflow_save','workflow',payload['id'])
        session.commit();return view(payload)
