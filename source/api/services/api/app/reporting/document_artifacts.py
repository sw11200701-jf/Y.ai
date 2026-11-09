"""One fixed renderer per real durable effect; no dummy finance dataset."""
from pathlib import Path
from datetime import datetime
from typing import Literal
import hashlib,json,os,re,time
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.db import Resource,get_resource,save_resource,utc_now
from app.auth import validate_context
from app.errors import AppError
from app.settings import settings
from app.execution.models import ExecutionRun,ExecutionStep,ExecutionBlob
from app.execution.effects import get_effect_context
from app.execution.storage import check_active_run
from app.data_access import authorize_data_resource,record_derivation,require_derivation_access
from .document_specs import validate_content
from .document_renderers import MIME

CONTRACT='generated-document-artifact-v1'


class DocumentArtifactPublic(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    id:str
    artifact_kind:Literal['generated_document']='generated_document'
    contract_version:Literal['generated-document-artifact-v1']=CONTRACT
    run_id:str
    execution_id:str
    node_key:str
    tool_id:Literal['document.word.v1','document.excel.v1','document.powerpoint.v1']
    renderer_version:str
    input_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    user_input_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    type:Literal['docx','xlsx','pptx']
    filename:str
    mime_type:str
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes:int=Field(ge=1,le=2097152)
    created_at:str
    data_sources:list[dict]


class DocumentArtifactStored(DocumentArtifactPublic):
    path:str
    read_proof:dict|None=Field(default=None,exclude=True)


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def input_identity(run):
    request=run.get('request',{})
    return digest({key:request.get(key) for key in ('prompt','inputs','message','agent_id','agent_version_id','workflow_id','workflow')})


def sources_for_run(run):
    sources=[];seen=set()
    for item in run.get('data_sources',[]):
        key=(item['kind'],item['resource_id'])
        if key not in seen:
            seen.add(key);sources.append({'kind':key[0],'resource_id':key[1],'action':'export_detail'})
    return sources


def authorize_sources(sources,*,session,context):
    validate_context(context,session)
    for source in sources:
        authorize_data_resource(session=session,context=context,kind=source['kind'],resource_id=source['resource_id'],action='export_detail')


def authorize_run_outputs(session,context,run):
    """Current export authority also guards cached run/model/approval reads."""
    if run.get('knowledge_csv_artifact_ids'):
        for identifier in run['knowledge_csv_artifact_ids']:
            body=session.scalar(select(Resource.payload).where(Resource.kind=='artifact',Resource.resource_id==identifier,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
            value=json.loads(body) if body else {}
            if value.get('artifact_kind')!='knowledge_csv' or value.get('run_id')!=run['id'] or identifier not in run.get('artifact_ids',[]):raise AppError('NOT_FOUND','CSV未绑定当前运行',404)
            authorize_sources(value['data_sources'],session=session,context=context)
    if not run.get('document_artifact_ids'):return
    authorize_sources(sources_for_run(run),session=session,context=context)
    for identifier in run['document_artifact_ids']:
        body=session.scalar(select(Resource.payload).where(Resource.kind=='artifact',Resource.resource_id==identifier,
            Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
        if body is None:raise AppError('NOT_FOUND','受控文档文件不存在或无权访问',404)
        value=DocumentArtifactStored.model_validate(json.loads(body))
        if value.run_id!=run['id'] or value.id not in run.get('artifact_ids',[]):raise AppError('NOT_FOUND','文档未绑定当前运行',404)
        authorize_sources(value.data_sources,session=session,context=context)


def preflight(*,tool_id,arguments,run_id,context,session):
    from app.rbac import require_any_permission,RUN_PERMISSIONS
    validate_context(context,session);require_any_permission(context,session,RUN_PERMISSIONS)
    run=get_resource(session,'run',run_id,context);check_active_run(session,run_id)
    authorize_sources(sources_for_run(run),session=session,context=context)
    validate_content(tool_id,arguments)


def issued_state(context,session):
    effect=get_effect_context()
    if effect is None or (effect.principal_id,effect.tenant_id,effect.workspace_id)!=(context.operator_id,context.tenant_id,context.workspace_id):
        raise AppError('DOCUMENT_EFFECT_REQUIRED','文档生成必须在当前调用者的真实持久副作用中执行',409)
    validate_context(context,session);check_active_run(session,effect.run_id)
    binding=session.get(ExecutionRun,effect.run_id,populate_existing=True)
    step=session.get(ExecutionStep,effect.execution_id,populate_existing=True)
    if (binding is None or binding.principal_id!=context.operator_id or binding.lease_until<time.time()
            or step is None or step.run_id!=effect.run_id or step.node_key!=effect.node_key
            or step.status!='issued' or not step.side_effect):
        raise AppError('DOCUMENT_EFFECT_REQUIRED','缺少活跃租约与已签发文档步骤',409)
    state=session.get(ExecutionBlob,binding.state_ref)
    if state is None or state.run_id!=effect.run_id:raise AppError('DOCUMENT_EFFECT_REQUIRED','缺少固定执行预算',409)
    deadline=json.loads(state.payload)['budget']['deadline_epoch']
    if time.time()>deadline:raise AppError('RUN_TIMEOUT','文档生成超过原始截止时间',504)
    run=get_resource(session,'run',effect.run_id,context)
    return effect,binding.generation,run


def generate(*,tool_id,arguments,context,session):
    from .document_tools import render_for_execution
    from .completed_documents import capture_source_closure,make_read_proof,digest as proof_digest
    effect,generation,run=issued_state(context,session)
    before_sources=sources_for_run(run);before_identity=input_identity(run)
    before_proof=capture_source_closure(session,context,before_sources)
    preflight(tool_id=tool_id,arguments=arguments,run_id=effect.run_id,context=context,session=session)
    identifier='doc_artifact_'+effect.execution_id
    if session.scalar(select(Resource.id).where(Resource.kind=='artifact',Resource.resource_id==identifier)):
        raise AppError('DOCUMENT_TARGET_EXISTS','已有生成记录，禁止重复生成或覆盖，请使用原完成结果',409)
    rendered=render_for_execution(tool_id=tool_id,arguments=arguments,context=context,session=session)
    _,current_generation,current=issued_state(context,session)
    if current_generation!=generation or sources_for_run(current)!=before_sources or input_identity(current)!=before_identity:
        raise AppError('DOCUMENT_CONTEXT_CHANGED','生成期间运行、来源或租约变化，未登记文件',409)
    if (rendered.tool_id!=tool_id or rendered.mime_type!=MIME[rendered.extension]
            or hashlib.sha256(rendered.content).hexdigest()!=rendered.sha256 or len(rendered.content)!=rendered.size_bytes):
        raise AppError('DOCUMENT_RENDER_INVALID','生成器结果完整性不符',409)
    if not re.fullmatch(r'[0-9a-f]{64}',effect.execution_id) or not re.fullmatch(r'run_[A-Za-z0-9_]+',effect.run_id):
        raise AppError('DOCUMENT_EFFECT_REQUIRED','服务器执行标识无效',409)
    root=settings.artifact_dir.resolve();directory=(root/'documents'/effect.run_id).resolve()
    if not directory.is_relative_to(root):raise AppError('DOCUMENT_PATH_INVALID','生成目录越界',409)
    directory.mkdir(parents=True,exist_ok=True)
    target=(directory/(effect.execution_id+'.'+rendered.extension)).resolve()
    stage=(directory/(effect.execution_id+'.stage')).resolve()
    if not target.is_relative_to(root) or not stage.is_relative_to(root):raise AppError('DOCUMENT_PATH_INVALID','生成文件越界',409)
    if target.exists() or stage.exists():raise AppError('DOCUMENT_TARGET_EXISTS','稳定执行目标已存在，禁止重写或自动认领',409)
    with stage.open('xb') as handle:
        handle.write(rendered.content);handle.flush();os.fsync(handle.fileno())
    # Exclusive atomic publication: an existing target can never be replaced.
    os.link(stage,target);stage.unlink()
    if target.stat().st_size!=rendered.size_bytes or hashlib.sha256(target.read_bytes()).hexdigest()!=rendered.sha256:
        raise AppError('DOCUMENT_RENDER_INVALID','写入后文件完整性不符',409)
    # No external/CPU work survives the writer/lease/source checks below.
    if session.bind.dialect.name=='sqlite':
        connection=session.connection()
        if not connection.connection.driver_connection.in_transaction:connection.exec_driver_sql('BEGIN IMMEDIATE')
    _,current_generation,current=issued_state(context,session)
    if current_generation!=generation or sources_for_run(current)!=before_sources or input_identity(current)!=before_identity:
        raise AppError('DOCUMENT_CONTEXT_CHANGED','登记前运行、来源或租约变化，未提交文件',409)
    authorize_sources(before_sources,session=session,context=context)
    if proof_digest(capture_source_closure(session,context,before_sources))!=proof_digest(before_proof):
        raise AppError('DOCUMENT_CONTEXT_CHANGED','生成期间原始来源内容、父链或权限变化，未登记文件',409)
    title=re.sub(r'[\x00-\x1f<>:"/\\|?*]','_',arguments['title']).strip(' .')[:80] or 'document'
    value=DocumentArtifactStored(id=identifier,run_id=effect.run_id,execution_id=effect.execution_id,node_key=effect.node_key,
        tool_id=tool_id,renderer_version=rendered.renderer_version,input_sha256=rendered.input_sha256,user_input_sha256=before_identity,
        type=rendered.extension,filename='文档-'+title+'.'+rendered.extension,mime_type=rendered.mime_type,sha256=rendered.sha256,
        size_bytes=rendered.size_bytes,created_at=utc_now(),path=str(target),data_sources=before_sources).model_dump(mode='json')
    # Private evidence is created by this writer only, never by a later read.
    value['read_proof']=make_read_proof(value,context,before_proof)
    save_resource(session,'artifact',identifier,value,context)
    if before_sources:record_derivation(session=session,context=context,kind='artifact',resource_id=identifier,sources=before_sources)
    for name in ('artifact_ids','document_artifact_ids'):
        ids=current.setdefault(name,[])
        if identifier not in ids:ids.append(identifier)
    save_resource(session,'run',effect.run_id,current,context)
    # Caller bridge commits Artifact + run list + step result under its fence.
    return {'artifact_ids':[identifier]}


def checked_artifact(*,artifact_id,context,session):
    require_derivation_access(session=session,context=context,kind='artifact',resource_id=artifact_id,completed_read=True)
    value=DocumentArtifactStored.model_validate(get_resource(session,'artifact',artifact_id,context))
    run=get_resource(session,'run',value.run_id,context)
    if value.read_proof is not None and run.get('status')=='succeeded':
        from .completed_documents import validate_completed_document_target
        row=session.scalar(select(Resource).where(Resource.kind=='artifact',Resource.resource_id==artifact_id,
            Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
        if row is None or validate_completed_document_target(session,context,row) is not True:
            raise AppError('NOT_FOUND','原始文档提交和来源见证不完整或当前权限不足',404)
    from app.execution.storage import authorize_dependencies
    authorize_dependencies(session,context,run,models=True)
    if artifact_id not in run.get('artifact_ids',[]) or artifact_id not in run.get('document_artifact_ids',[]) or input_identity(run)!=value.user_input_sha256:
        raise AppError('NOT_FOUND','文件不属于固定运行输入',404)
    binding=session.get(ExecutionRun,value.run_id)
    step=session.get(ExecutionStep,value.execution_id)
    expected=hashlib.sha256((value.run_id+':'+binding.dsl_hash+':'+value.node_key).encode()).hexdigest() if binding else None
    if step is None or expected!=value.execution_id or step.run_id!=value.run_id or step.status!='succeeded' or not step.side_effect or not step.result_ref:
        raise AppError('DOCUMENT_NOT_COMMITTED','生成文件尚未完成持久提交',409)
    blob=session.get(ExecutionBlob,step.result_ref)
    if blob is None or blob.run_id!=value.run_id:raise AppError('DOCUMENT_NOT_COMMITTED','缺少真实文件结果账本',409)
    state=json.loads(blob.payload);slots=state.get('agents',{}).values()
    receipts=[r for slot in slots for r in slot.get('document_artifact_receipts',[])]+state.get('document_node_receipts',{}).get(value.execution_id,[])
    if not any(r.get('artifact_id')==artifact_id and r.get('sha256')==value.sha256 and r.get('execution_id')==value.execution_id for r in receipts):
        raise AppError('DOCUMENT_NOT_COMMITTED','文件与真实工具步骤结果不符',409)
    authorize_sources(value.data_sources,session=session,context=context)
    path=Path(value.path).resolve();root=(settings.artifact_dir.resolve()/'documents'/value.run_id).resolve()
    if not root.is_relative_to(settings.artifact_dir.resolve()) or not path.is_relative_to(root) or not path.is_file():
        raise AppError('NOT_FOUND','生成文件不存在或目录无效',404)
    if value.mime_type!=MIME[value.type] or path.stat().st_size!=value.size_bytes or hashlib.sha256(path.read_bytes()).hexdigest()!=value.sha256:
        raise AppError('CONFLICT','生成文件完整性校验失败',409)
    return value


def tool_catalog():
    from .document_tools import document_tool_catalog
    result=document_tool_catalog()
    for item in result['items']:
        item.update(execution_available=True,execution_path='simple_model_agent.document_tools',artifact_contract_version=CONTRACT)
        item.update(execution_paths=['simple_model_agent.document_tools','workflow.document_generate','deterministic_document_form'],
            deterministic_run_url='/api/v1/tools/document-generators/'+item['id']+'/runs',workflow_node_type='document_generate')
        item.pop('unavailable_reason',None)
        item['timeout_policy']='bounded synchronous renderer; deadline/lease checked before and after, no forced CPU termination'
    return result
