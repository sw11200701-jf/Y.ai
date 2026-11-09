import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, Query, Request, Header
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.context import get_context
from app.contracts import BusinessContext, WorkflowDefinition, RunCreate
from app.db import get_session, get_resource, list_resources, save_resource, new_id, RunEventRecord
from app.errors import AppError
from app.settings import settings
from app.runner.service import scheduler
from app.runner.validation import validate_workflow
from app.execution.drafts import DebugRunCreate,DebugTargetRequest,DebugTargetDescription
from app.execution.trace import TracePage
from app.execution.knowledge_directory import KnowledgeDirectoryCapabilities

router = APIRouter()

@router.get("/run-approvers")
def run_approvers(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.rbac import require_any_permission, RUN_PERMISSIONS, user_policy
    from app.auth_models import User
    require_any_permission(context, session, RUN_PERMISSIONS)
    items=[]
    for user in session.scalars(select(User).where(User.tenant_id==context.tenant_id, User.workspace_id==context.workspace_id, User.active==True)):
        role,permissions=user_policy(user,session)
        if "runs.approve" in permissions: items.append({"id":user.id,"display_name":user.display_name,"role_name":role.name,"is_self":user.id==context.operator_id})
    return {"items":items}

@router.get("/approvals")
def approval_inbox(status: Literal["pending","approved","rejected","expired","all"]="pending", session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.approvals import approval_inbox as get_inbox
    return get_inbox(session, context, status)

@router.get("/workflows/node-registry")
def registry():
    from app.runner.registry import node_registry
    return node_registry()

@router.post("/workflows/validate")
def validate(body: WorkflowDefinition):
    nodes = validate_workflow(body)
    return {"valid": True, "ordered_node_ids": [n.id for n in nodes]}

@router.get("/workflows/variable-catalog")
def variable_catalog(context: BusinessContext = Depends(get_context)):
    from app.execution.drafts import variable_catalog as catalog
    return catalog(context)

@router.get("/workflows/knowledge-directory-capabilities", response_model=KnowledgeDirectoryCapabilities)
def knowledge_directory_capabilities(session:Session=Depends(get_session), context:BusinessContext=Depends(get_context)):
    from app.execution.knowledge_directory import capabilities
    return capabilities(context,session)

class NodeDraftCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    workflow:WorkflowDefinition
    node_id:str
    scope_path:list[str]=Field(default_factory=list,max_length=3)
@router.post('/workflows/node-drafts',status_code=201)
def create_node_draft(body:NodeDraftCreate,context:BusinessContext=Depends(get_context)):
    from app.execution.node_settings import persist
    return persist(context,body=body.workflow,draft_node_id=body.node_id,draft_scope=body.scope_path)

@router.get("/workflows")
def workflows(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.node_settings import view
    from app.resource_policy import directory
    safe=directory(session,context,'workflow');access={item['id']:item['access'] for item in safe}
    full=[{**view(item),'access':access.get(item['id'],{})} for item in list_resources(session,'workflow',context)]
    ids={item['id'] for item in full}
    return {'items':full+[item for item in safe if item['id'] not in ids]}

@router.post("/workflows/debug-runs", status_code=202)
async def debug_run(body: DebugRunCreate, request: Request, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.api import project_run
    submitted = body.request().model_copy(update={"request_id": getattr(request.state, "request_id", None)})
    return project_run(session, context, await scheduler.submit(submitted, context, session))

@router.post("/workflows/debug-target", response_model=DebugTargetDescription)
def debug_target(body:DebugTargetRequest, session:Session=Depends(get_session), context:BusinessContext=Depends(get_context)):
    from app.rbac import require_any_permission,RUN_PERMISSIONS
    from app.execution.drafts import describe_target
    require_any_permission(context,session,RUN_PERMISSIONS)
    return describe_target(body)

@router.post("/workflows", status_code=201)
def save_workflow(body: WorkflowDefinition, context: BusinessContext = Depends(get_context)):
    from app.execution.node_settings import persist
    return persist(context,body=body)

@router.get("/workflows/{workflow_id}")
def workflow_detail(workflow_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.node_settings import view
    from app.resource_policy import capabilities
    return {**view(get_resource(session,"workflow",workflow_id,context)),"access":capabilities(session,context,"workflow",workflow_id)}

@router.put("/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: WorkflowDefinition, if_match: str | None = Header(None), context: BusinessContext = Depends(get_context)):
    from app.execution.node_settings import persist
    supplied=getattr(body,'expected_graph_sha256',None) or getattr(body,'graph_sha256',None)
    header=if_match.strip('"') if if_match else None
    if header and supplied and header!=supplied:raise AppError('WORKFLOW_REVISION_CONFLICT','保存版本不一致',409)
    return persist(context,workflow_id=workflow_id,body=body,expected=header or supplied)

from app.execution.node_settings import NodeSettingsSave
@router.put("/workflows/{workflow_id}/node-settings")
def save_node_settings(workflow_id: str, body: NodeSettingsSave, context: BusinessContext = Depends(get_context)):
    from app.execution.node_settings import persist
    return persist(context,workflow_id=workflow_id,node_settings=body,expected=body.expected_graph_sha256)

@router.get("/runs")
def runs(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.run_directory import directory
    return {"items":directory(session,context)}

@router.post("/runs", status_code=202)
async def create_run(body: RunCreate, request: Request, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.api import project_run
    submitted=body.model_copy(update={"request_id":getattr(request.state,"request_id",None)})
    return project_run(session, context, await scheduler.submit(submitted, context, session))

@router.get("/runs/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.api import project_run
    return project_run(session, context, get_resource(session, "run", run_id, context))

@router.get("/runs/{run_id}/state")
async def run_state(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.runtime import state_view
    return await state_view(session, context, run_id)

@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.api import resume
    return await resume(scheduler, session, context, run_id)

class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)

@router.get("/runs/{run_id}/approvals")
def approvals(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.approvals import list_approvals, public_approval
    return {"items": [public_approval(row) for row in list_approvals(session, context, run_id)]}

@router.post("/runs/{run_id}/approvals/{approval_id}/decision")
def decision(run_id: str, approval_id: str, body: ApprovalDecision, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.approvals import decide
    return decide(session, context, run_id, approval_id, body.decision, body.note)

@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.api import project_run
    return project_run(session, context, await scheduler.cancel(run_id, context, session))

@router.get("/runs/{run_id}/events")
def events(run_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.trace import authorize_view, clean, bounded
    authorize_view(session, context, run_id)
    rows = list(session.scalars(select(RunEventRecord).where(RunEventRecord.run_id == run_id, RunEventRecord.tenant_id == context.tenant_id, RunEventRecord.workspace_id == context.workspace_id, RunEventRecord.sequence > after).order_by(RunEventRecord.sequence).limit(limit+1)))
    items=[]
    for row in rows[:limit]:
        value={"id":row.id,**clean(json.loads(row.payload))}
        value["data"]=bounded(value.get("data",{}),32768)
        if len(json.dumps(items+[value],ensure_ascii=False).encode())>258048:break  # include response envelope safely
        items.append(value)
    session.expire_all()
    authorize_view(session, context, run_id)
    return {"items": items, "last_sequence": items[-1]["sequence"] if items else after, "has_more": len(rows)>len(items)}

@router.get("/runs/{run_id}/trace", response_model=TracePage)
def trace(run_id: str, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.execution.trace import trace_view
    return trace_view(session, context, run_id, after, limit)

@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return get_resource(session, "dataset", dataset_id, context)

@router.get("/runs/{run_id}/artifacts")
def artifacts(run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    run = get_resource(session, "run", run_id, context)
    from app.reporting.document_artifacts import checked_artifact
    for identifier in run.get('document_artifact_ids',[]):checked_artifact(artifact_id=identifier,context=context,session=session)
    from app.knowledge.slicing_finalizer import checked_csv
    for identifier in run.get('knowledge_csv_artifact_ids',[]):checked_csv(identifier,context=context,session=session)
    return {"items": [{k: v for k, v in get_resource(session, "artifact", aid, context).items() if k not in {"path","read_proof"}} for aid in run["artifact_ids"]]}

@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.data_access import require_derivation_access
    require_derivation_access(session=session,context=context,kind="artifact",resource_id=artifact_id,completed_read=True)
    artifact = get_resource(session, "artifact", artifact_id, context)
    if artifact.get('artifact_kind')=='knowledge_csv':
        from app.knowledge.slicing_finalizer import checked_csv
        value,_=checked_csv(artifact_id,context=context,session=session)
        return FileResponse(value['path'],filename=value['filename'],media_type=value['mime_type'],headers={'Cache-Control':'no-store'})
    if artifact.get('artifact_kind')=='generated_document':
        from app.reporting.document_artifacts import checked_artifact
        document=checked_artifact(artifact_id=artifact_id,context=context,session=session)
        return FileResponse(document.path,filename=document.filename,media_type=document.mime_type)
    run = get_resource(session, "run", artifact["run_id"], context)
    get_resource(session,"dataset",artifact["dataset_id"],context)
    datasets = set(run.get("dataset_ids", [])) | {run.get("dataset_id")}
    if artifact_id not in run["artifact_ids"] or artifact["dataset_id"] not in datasets:
        raise AppError("NOT_FOUND", "Artifact is not attached to this run", 404)
    path = Path(artifact["path"]).resolve()
    if not path.is_relative_to(settings.artifact_dir.resolve()) or not path.is_file():
        raise AppError("NOT_FOUND", "Artifact file unavailable", 404)
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise AppError("CONFLICT", "Artifact integrity check failed", 409)
    return FileResponse(path, filename=artifact["filename"])
