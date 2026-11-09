"""Application callbacks over the existing durable scheduler and resource ACLs."""
import asyncio,json,time,hashlib
from pathlib import Path
from copy import deepcopy
from sqlalchemy import select
from fastapi.responses import StreamingResponse,Response
from app.db import SessionLocal,Resource,RunEventRecord,new_id,utc_now,get_resource,save_resource
from app.errors import AppError
from app.machine_auth import context_from_authority,require_scope,assert_run_partition,authorize_application_result,assert_release_dependencies
from app.machine_models import MachineRunAuthorization
from app.execution.models import ExecutionRun,ExecutionStep
from app.contracts import RunCreate,WorkflowDefinition,MessageEnvelope
from app.settings import settings
from app.application_release import prepare_release,authorize_resources,client_options

TERMINAL={'succeeded','failed','cancelled','interrupted'}


def _current(session,authority,scope):
    context=context_from_authority(session,authority)
    return context,require_scope(session,context,scope).model_dump(mode='json')


def _usage(session,run_id):
    from app.external_api.observability import usage_for_run
    return usage_for_run(session,run_id)


def _project(session,context,authority,run_id,request_id,replayed=False,scope='runs.read'):
    from app.external_api import bridge
    from app.external_api.observability import timing_for_run
    run=authorize_application_result(context,run_id,session=session,operation=scope)
    bundle=bridge.load_release(session,authority=authority)
    result=bridge.project_run(run,authority=authority,outputs=run.get('public_outputs',run.get('outputs',{})),usage=_usage(session,run_id),
        output_schema=bundle['output_schema'],request_id=request_id,replayed=replayed,timing=timing_for_run(session,run_id))
    if run.get('conversation_id'):result['conversation_id']=run['conversation_id']
    return result


def _admit(authority,invocation,idempotency_key,request_id):
    from app.external_api import bridge,conversations,files
    from app.external_api.storage import write_gate
    from app.external_api.values import canonical_payload_digest
    from app.messages import resolve_message
    from app.runner.service import scheduler
    operation='chat.invoke' if 'message' in invocation else 'workflow.invoke'
    body={key:value for key,value in invocation.items() if key not in {'wait_seconds','user'}}
    with SessionLocal() as session:
        # Acquire the SQLite writer BEFORE any authority/snapshot read. No
        # inherited deferred snapshot can be promoted after another admission.
        write_gate(session)
        context,authority=_current(session,authority,operation)
        bundle=assert_release_dependencies(session,context)
        from app.application_files import contains_file_schema
        if contains_file_schema(bundle['output_schema']):require_scope(session,context,'files.read')
        expected='agent' if operation=='chat.invoke' else 'workflow'
        if bundle['source_identity']['kind']!=expected:raise AppError('APP_INVOCATION_INVALID','调用方式与应用发布类型不匹配',422)
        proposed=new_id('run')
        reservation=bridge.reserve_invocation(session,authority=authority,operation=operation,idempotency_key=idempotency_key,
            body_hash=canonical_payload_digest(body),proposed_run_id=proposed,request_id=request_id)
        if reservation['replayed']:
            result=_project(session,context,authority,reservation['run_id'],request_id,True,operation)
            session.commit();return result,None
        message=deepcopy(invocation.get('message'))
        history=[];conversation_id=None;history_sources=[]
        if message is not None:
            if invocation.get('conversation_id'):
                history=conversations.history(session,authority=authority,conversation_id=invocation['conversation_id'],
                    authorize_run=lambda db,authority,run_id:authorize_application_result(context,run_id,session=db,operation='chat.invoke'))['messages']
                from app.external_api.models import ConversationMessage
                from app.data_models import DataDerivation
                previous=list(session.scalars(select(ConversationMessage.run_id).where(ConversationMessage.conversation_id==invocation['conversation_id'],
                    ConversationMessage.role=='assistant').order_by(ConversationMessage.sequence.desc()).limit(5)))
                seen=set()
                def inherit_sources(row):
                    if row.id in seen:return
                    if len(seen)>=128:raise AppError('RESOURCE_LIMIT','会话来源链超过安全范围',422)
                    seen.add(row.id)
                    for source in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk==row.id)):
                        dependency=session.get(Resource,source.source_pk)
                        if dependency is None:raise AppError('NOT_FOUND','历史会话来源已不可访问',404)
                        if dependency.kind=='run':inherit_sources(dependency)
                        else:
                            item={'kind':dependency.kind,'resource_id':dependency.resource_id,'action':source.required_action}
                            if item not in history_sources:history_sources.append(item)
                for previous_id in previous:
                    prior=session.scalar(select(Resource).where(Resource.kind=='run',Resource.resource_id==previous_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
                    if prior is None:raise AppError('NOT_FOUND','历史会话已不可访问',404)
                    inherit_sources(prior)
            for index,file_id in enumerate(message.get('attachment_ids',[])):
                row=files.resolve_file(session,authority=authority,file_id=file_id)
                get_resource(session,row.resource_kind,row.resource_id,context)
                if row.resource_kind!='attachment':raise AppError('APP_INPUT_INVALID','消息需要受控上传附件',422)
                message['attachment_ids'][index]=row.resource_id
            record=conversations.begin_message(session,authority=authority,conversation_id=invocation.get('conversation_id'),run_id=proposed,message=invocation['message'])
            conversation_id=record['conversation_id']
        request=RunCreate(workflow=WorkflowDefinition.model_validate(bundle['workflow_snapshot']),prompt=message.get('text','') if message else '',
            inputs=invocation.get('inputs',{}),run_kind='application',message=MessageEnvelope(**message,inputs=invocation.get('inputs',{})) if message else None)
        # Existing resolver validates message modalities/attachments. It does no
        # provider calls and receives the real partition-bound machine context.
        resolved=asyncio.run(resolve_message(message=request.message,prompt=request.prompt,inputs=request.inputs,context=context,session=session)) if message else {
            'type':'text','text':'','inputs':request.inputs,'attachment_ids':[],'attachments':[],'source_refs':[],
            'capabilities':{'requires_vision':False,'requires_audio':False,'requires_document_extraction':False}}
        application={'bundle':bundle,'release_sha256':canonical_payload_digest(bundle,maximum_bytes=1048576,maximum_depth=32),
            'reservation_id':reservation['reservation_id'],'request_id':request_id,'conversation_id':conversation_id,'history':history,'history_sources':history_sources}
        asyncio.run(scheduler._submit(request,context,session,frozen_agents=bundle['agent_snapshots'],application=application,
            prepared_message=resolved,proposed_run_id=proposed,commit=False,enqueue=False))
        # Projection also checks current output/source access before COMMIT.
        result=_project(session,context,authority,proposed,request_id,scope=operation)
        session.commit()
        return result,context


async def submit(*,authority,invocation,idempotency_key,request_id):
    from app.runner.service import scheduler
    result,context=await asyncio.to_thread(_admit,authority,invocation,idempotency_key,request_id)
    if context is not None:scheduler.enqueue_committed(result['run_id'],context)
    deadline=time.monotonic()+invocation.get('wait_seconds',0)
    scope='chat.invoke' if 'message' in invocation else 'workflow.invoke'
    while result['status'] not in TERMINAL and time.monotonic()<deadline:
        await asyncio.sleep(min(.1,max(0,deadline-time.monotonic())))
        result=await asyncio.to_thread(_read,authority,result['run_id'],request_id,scope,result.get('replayed',False))
    return result


def _read(authority,run_id,request_id,scope='runs.read',replayed=False):
    with SessionLocal() as session:
        context,authority=_current(session,authority,scope)
        return _project(session,context,authority,run_id,request_id,replayed,scope)


async def get_run(*,authority,run_id,request_id):return await asyncio.to_thread(_read,authority,run_id,request_id)


def _cancel(authority,run_id,request_id):
    from app.external_api.storage import write_gate
    from app.runner.service import emit
    with SessionLocal() as session:
        write_gate(session);context,authority=_current(session,authority,'runs.cancel')
        run=authorize_application_result(context,run_id,session=session,operation='runs.cancel')
        if run['status'] not in TERMINAL:
            issued=session.scalar(select(ExecutionStep).where(ExecutionStep.run_id==run_id,ExecutionStep.side_effect==True,ExecutionStep.status=='issued').limit(1))
            status='outcome_unknown' if issued else 'cancelled'
            run.update(status=status,updated_at=utc_now())
            save_resource(session,'run',run_id,run,context);emit(session,run_id,context,'effect.unknown' if issued else 'run.cancelled',status=status)
            settle_run(session,run_id,run)
        result=_project(session,context,authority,run_id,request_id,scope='runs.cancel');session.commit();return result


async def cancel(*,authority,run_id,request_id):
    from app.runner.service import scheduler
    result=await asyncio.to_thread(_cancel,authority,run_id,request_id)
    if run_id in scheduler.active:scheduler.active[run_id].cancel()
    return result


async def resume(*,authority,run_id,request_id):
    from app.runner.service import scheduler
    from app.execution.api import resume as resume_existing
    # Existing resume verifies checkpoint, approval, terminal/unknown-effect and
    # original budget before rebinding only this machine's executing key.
    with SessionLocal() as session:
        context,authority=_current(session,authority,'runs.resume')
        if not authority['runtime_policy']['allow_resume']:raise AppError('APP_SCOPE_DENIED','发布版未开放恢复',403)
        await resume_existing(scheduler,session,context,run_id)
        return _project(session,context,authority,run_id,request_id,scope='runs.resume')


def _trace(authority,run_id,request_id):
    from app.external_api import bridge
    with SessionLocal() as session:
        context,authority=_current(session,authority,'trace.read')
        run=authorize_application_result(context,run_id,session=session,operation='trace.read')
        steps=list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id==run_id).limit(256)))
        return bridge.project_trace({'status':run['status'],'steps':[{'status':step.status} for step in steps]},authority=authority)


async def trace(*,authority,run_id,request_id):return await asyncio.to_thread(_trace,authority,run_id,request_id)


def _poll(authority,run_id,cursor,lease_id=None):
    from app.external_api import bridge,streams
    from app.external_api.storage import write_gate
    with SessionLocal() as session:
        write_gate(session);context,authority=_current(session,authority,'events.read')
        run=authorize_application_result(context,run_id,session=session,operation='events.read')
        if lease_id:streams.renew(session,authority=authority,lease_id=lease_id)
        else:lease_id=streams.acquire(session,authority=authority,run_id=run_id)['lease_id']
        bundle=bridge.load_release(session,authority=authority)
        rows=list(session.scalars(select(RunEventRecord).where(RunEventRecord.run_id==run_id,RunEventRecord.sequence>cursor).order_by(RunEventRecord.sequence).limit(50)))
        # This snapshot is used only for the current single next yield. Another
        # fresh short transaction is required before the next payload is sent.
        projected=[]
        for row in rows:
            value=bridge.project_event(json.loads(row.payload),authority=authority,outputs=run.get('public_outputs',run.get('outputs',{})),usage=_usage(session,run_id),output_schema=bundle['output_schema'])
            if value is not None:projected.append(value)
        next_cursor=rows[-1].sequence if rows else cursor
        session.commit();return projected,next_cursor,run['status'],lease_id


async def events(*,authority,run_id,last_event_id,request_id,request):
    # Authorize/acquire before response headers so failure remains a JSON error.
    first=await asyncio.to_thread(_poll,authority,run_id,last_event_id)
    async def generate():
        cursor=last_event_id;lease_id=first[3]
        try:
            while not await request.is_disconnected():
                values,next_cursor,status,_=await asyncio.to_thread(_poll,authority,run_id,cursor,lease_id)
                if values:
                    value=values[0];cursor=value['sequence']
                    yield f"id: {cursor}\nevent: {value['type']}\ndata: {json.dumps(value,ensure_ascii=False,separators=(',',':'))}\n\n"
                    if value['type']=='result':break
                else:
                    cursor=next_cursor
                    if status in TERMINAL or status=='outcome_unknown':break
                    yield ': keep-alive\n\n'
                    await asyncio.sleep(.5)
        except AppError as exc:
            # A revoked connection emits no further business information.
            yield 'event: error\ndata: '+json.dumps({'code':exc.code,'request_id':request_id})+'\n\n'
        finally:
            def cleanup():
                from app.external_api import streams
                with SessionLocal() as session:streams.release(session,lease_id=lease_id);session.commit()
            await asyncio.to_thread(cleanup)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


def _conversation(authority,conversation_id):
    from app.external_api import conversations
    with SessionLocal() as session:
        context,authority=_current(session,authority,'conversations.read')
        return conversations.history(session,authority=authority,conversation_id=conversation_id,
            authorize_run=lambda db,authority,run_id:authorize_application_result(context,run_id,session=db,operation='conversations.read'))


async def conversation(*,authority,conversation_id,request_id):return await asyncio.to_thread(_conversation,authority,conversation_id)


def _store_file(authority,filename,declared,data):
    from app.attachments import validate_content
    from app.external_api import files
    from app.external_api.storage import write_gate
    path=None
    try:
        with SessionLocal() as session:
            write_gate(session);context,authority=_current(session,authority,'files.upload')
            assert_release_dependencies(session,context)
            if len(data)>min(10485760,authority['runtime_policy']['max_file_bytes']):raise AppError('APP_REQUEST_TOO_LARGE','文件大小超过应用限制',413)
            mime,kind=validate_content(filename,declared,data)
            policy=authority['runtime_policy']
            category=kind if kind in {'image','audio'} else {'.pdf':'pdf','.docx':'docx','.xlsx':'xlsx','.csv':'csv'}.get(Path(filename).suffix.lower(),'text')
            if len(data)>policy['max_file_bytes'] or category not in policy['allowed_file_types']:raise AppError('APP_FILE_POLICY_DENIED','文件类型或大小未获此应用许可',422)
            identifier=new_id('attachment');folder=settings.data_dir/'attachments';folder.mkdir(parents=True,exist_ok=True)
            path=folder/(identifier+'.bin');path.write_bytes(data)
            payload={'id':identifier,'filename':filename,'mime':mime,'media_kind':kind,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'created_at':utc_now(),'path':str(path.resolve())}
            save_resource(session,'attachment',identifier,payload,context)
            result=files.register_file(session,authority=authority,resource_kind='attachment',resource_id=identifier,filename=filename,
                media_type=mime,size_bytes=len(data),sha256=payload['sha256'])
            session.commit();return result
    except BaseException:
        if path:path.unlink(missing_ok=True)
        raise


async def upload(*,authority,file,request_id):
    data=await file.read(10485761)
    import re
    if re.search(rb'zpa_[0-9a-f]{16}_[A-Za-z0-9_-]{43,86}',data):raise AppError('APP_INPUT_INVALID','上传内容不能包含应用凭据',422)
    return await asyncio.to_thread(_store_file,authority,file.filename or '',file.content_type,data)


def _file(authority,file_id,content=False):
    from app.external_api import files
    from app.attachments import load_attachment
    from app.data_access import require_derivation_access
    with SessionLocal() as session:
        context,authority=_current(session,authority,'files.read')
        assert_release_dependencies(session,context)
        row=files.resolve_file(session,authority=authority,file_id=file_id)
        get_resource(session,row.resource_kind,row.resource_id,context)
        require_derivation_access(session=session,context=context,kind=row.resource_kind,resource_id=row.resource_id)
        if not content:return files.metadata(row)
        if row.resource_kind=='artifact':
            from app.application_files import artifact_bytes
            _,data,_=artifact_bytes(session,context,row.resource_id)
        else:_,data=load_attachment(attachment_id=row.resource_id,context=context,session=session)
        if len(data)!=row.size_bytes or hashlib.sha256(data).hexdigest()!=row.sha256:raise AppError('CONFLICT','文件登记校验信息发生变化',409)
        return data,row.media_type,row.filename


async def file_metadata(*,authority,file_id,request_id):return await asyncio.to_thread(_file,authority,file_id)


async def download(*,authority,file_id,request_id):
    from urllib.parse import quote
    data,mime,filename=await asyncio.to_thread(_file,authority,file_id,True)
    return Response(data,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'no-store'})


def settle_run(session,run_id,run):
    """Bookkeeping runs in the status transaction, even after a key is revoked."""
    binding=session.get(MachineRunAuthorization,run_id)
    if binding is None:return
    from app.external_api import bridge
    from app.external_api.models import InvocationReservation
    reservation=session.scalar(select(InvocationReservation).where(InvocationReservation.run_id==run_id))
    if reservation is None or reservation.settled:return
    if run['status']=='succeeded':
        from app.machine_auth import fresh_run_context
        from app.application_files import normalize_outputs
        from app.external_api.projection import public_outputs
        context=fresh_run_context(session,run_id);authority=require_scope(session,context,'chat.invoke' if run.get('conversation_id') else 'workflow.invoke').model_dump(mode='json')
        bundle=assert_release_dependencies(session,context)
        outputs=normalize_outputs(session,context,authority,run.get('outputs',{}),bundle['output_schema'])
        run['public_outputs']=public_outputs(outputs,bundle['output_schema'])
        row=session.scalar(select(Resource).where(Resource.kind=='run',Resource.resource_id==run_id,Resource.owner_id==binding.principal_id))
        row.payload=json.dumps(run,ensure_ascii=False,allow_nan=False)
    from app.machine_models import MachineStepUsage
    rows=list(session.scalars(select(MachineStepUsage).where(MachineStepUsage.run_id==run_id)))
    known=all(row.status=='known' for row in rows) and (bool(rows) or not run.get('model_bindings'))
    # A provider may report total only, or a partial breakdown. A component
    # absent from any actual attempt must stay absent from the aggregate.
    counts=[json.loads(row.tokens) for row in rows]
    tokens={key:sum(item[key] for item in counts) for key in ('prompt_tokens','completion_tokens','total_tokens') if counts and all(key in item for item in counts)}
    if not rows and not run.get('model_bindings'):tokens={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
    usage={'status':'known' if known else 'partial' if tokens else 'unknown','tokens':tokens,
        'source':'no_model_calls' if not rows and not run.get('model_bindings') else 'provider_reported' if rows else 'unavailable'}
    bridge.settle_invocation(session,run_id=run_id,status=run['status'],usage=usage,usage_unknown=not known)
    if run.get('conversation_id') and run['status'] in TERMINAL:
        from app.external_api.models import Conversation,ConversationMessage,ApplicationVersion
        from app.external_api.projection import public_outputs
        row=session.get(Conversation,run['conversation_id'],populate_existing=True)
        if row and row.active_run_id==run_id:
            if run['status']=='succeeded':
                version=session.get(ApplicationVersion,binding.version_id)
                answer=public_outputs(run.get('public_outputs',run.get('outputs',{})),json.loads(version.bundle)['output_schema'])
                row.revision+=1
                session.add(ConversationMessage(id=new_id('appmsg'),conversation_id=row.id,run_id=run_id,sequence=row.revision,role='assistant',
                    content=json.dumps(answer,ensure_ascii=False,allow_nan=False),created_at=utc_now()))
            row.active_run_id=None


def model_attempt(execution):
    """A durable usage intent is independent of provider response availability."""
    if getattr(execution.context,'principal_type','human')!='service':return
    from app.machine_models import MachineStepUsage
    if execution.session.get(MachineStepUsage,execution.execution_id) is None:
        execution.session.add(MachineStepUsage(execution_id=execution.execution_id,run_id=execution.run_id,status='unknown',tokens='{}'))
    execution.session.commit()


def model_usage(execution,usage):
    if getattr(execution.context,'principal_type','human')!='service':return
    from app.machine_models import MachineStepUsage
    from app.external_api.values import bounded_reported_usage
    value=bounded_reported_usage(usage)
    row=execution.session.get(MachineStepUsage,execution.execution_id)
    if row is not None:row.status=value['status'];row.tokens=json.dumps(value['tokens'])


def configure():
    from app.external_api.callbacks import configure as register
    from app.machine_auth import provision_principal
    register(prepare_release=prepare_release,authorize_resources=authorize_resources,provision_principal=provision_principal,client_options=client_options,
        **{name:globals()[name] for name in ('submit','get_run','events','cancel','resume','trace','conversation','upload','file_metadata','download')})
