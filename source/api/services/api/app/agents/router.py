from fastapi import APIRouter, Depends, Query, Response
from threading import RLock
from sqlalchemy.orm import Session

from app.context import get_context
from app.contracts import BusinessContext, RunCreate
from app.db import get_resource, get_session, list_resources
from .service import AgentWrite, agent_references, execute_agent, list_agents, list_conversations, publish_agent, save_agent
from .specs import AgentDebug
from .model_adapter import model_status
from app.plugins.spec_base import StrictModel
from .workflows import claim_workflow, workflow_snapshot
from app.db import save_resource, utc_now
from .types import agent_type, agent_view, validate_type
from app.plugins.permissions import require_page
from app.errors import AppError
from app.plugins.validation import ValidationRoute

router = APIRouter(tags=["agents"], route_class=ValidationRoute)
_publish_lock = RLock()


@router.get("/agents")
def agents(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=200),
           status: str = Query("", pattern="^(|draft|published)$"),
           session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return list_agents(context=context, session=session, page=page, page_size=page_size, q=q, status=status)


@router.get("/agents/conversations")
def conversations(session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return list_conversations(context=context, session=session)


@router.get("/agents/model-status")
def model_configuration(session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return model_status(context=context, session=session)


@router.post("/agents", status_code=201)
def create(data: AgentWrite, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return save_agent(data, context, session)


@router.get('/agents/capabilities')
def agent_capabilities(session: Session = Depends(get_session,scope='function'),context: BusinessContext = Depends(get_context)):
    from app.plugins.permissions import require_ai_access
    from .document_bindings import CAPABILITIES as DOCUMENT_CAPABILITIES
    from .skill_bindings import CAPABILITIES as SKILL_CAPABILITIES
    require_ai_access(context,session)
    return {**DOCUMENT_CAPABILITIES,**SKILL_CAPABILITIES}


@router.get("/agents/{agent_id}")
def get(agent_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    from app.resource_policy import capabilities
    return {**agent_view(get_resource(session,"agent",agent_id,context)),"access":capabilities(session,context,"agent",agent_id)}


@router.put("/agents/{agent_id}")
def update(agent_id: str, data: AgentWrite, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return save_agent(data, context, session, agent_id)


@router.post("/agents/{agent_id}/debug")
async def debug(agent_id: str, data: AgentDebug, response: Response, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    from .service import get_agent_config
    agent = get_agent_config(agent_id=agent_id, agent_version_id=data.agent_version_id, context=context, session=session)
    validate_type(agent, executing=True)
    if agent.get("main_workflow_id"):
        from app.runner.service import scheduler
        run = await scheduler.submit(RunCreate(agent_id=agent_id, agent_version_id=data.agent_version_id,
            prompt=data.prompt or "调试主流程", query=data.query, inputs=data.inputs, message=data.message), context, session)
        response.status_code = 202
        return {**run, "run_id": run["id"], "debug_mode": "workflow_run"}
    return await execute_agent(agent_id=agent_id, prompt=data.prompt, query=data.query, inputs=data.inputs,
                               context=context, session=session, run_id="debug", agent_version_id=data.agent_version_id, message=data.message)


class MainWorkflow(StrictModel):
    workflow_id: str


@router.put("/agents/{agent_id}/workflow")
def bind_workflow(agent_id: str, data: MainWorkflow, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    require_page(context, session, "page.ai")
    agent = get_resource(session, "agent", agent_id, context, access="write")
    if agent.get("agent_type") == "simple":
        raise AppError("VALIDATION_ERROR", "请先将AI智能切换为高阶类型，再绑定主流程", 422)
    proposed = {**agent, "agent_type": "advanced", "main_workflow_id": data.workflow_id, "status": "draft", "updated_at": utc_now()}
    if data.workflow_id:
        workflow_snapshot(proposed, context, session)
    claim_workflow(agent_id, data.workflow_id, context, session)
    save_resource(session, "agent", agent_id, proposed, context)
    return proposed


@router.post("/agents/{agent_id}/publish", status_code=201)
def publish(agent_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    with _publish_lock:
        result = publish_agent(agent_id=agent_id, context=context, session=session)
        session.commit()
        return result


@router.get("/agents/{agent_id}/versions")
def versions(agent_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    get_resource(session, "agent", agent_id, context)
    return {"items": sorted([v for v in list_resources(session, "agent_version", context)
                             if v["agent_id"] == agent_id and v.get("status") == "published"], key=lambda v: v["version"])}


@router.get("/agents/{agent_id}/references")
def references(agent_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return {"items": agent_references(agent_id=agent_id, context=context, session=session)}
