"""Metadata only until central durable Artifact registration is paired."""
from fastapi import APIRouter, Depends
from app.context import get_context
from app.db import get_session
from app.plugins.permissions import require_tool_access
from .document_artifacts import tool_catalog,DocumentArtifactPublic,checked_artifact

router = APIRouter(tags=['document-tools'])
from .document_nodes import router as document_jobs_router
router.include_router(document_jobs_router)

@router.get('/tools/document-generators')
def catalog(context=Depends(get_context), session=Depends(get_session, scope='function')):
    require_tool_access(context, session)
    return tool_catalog()


@router.get('/document-artifacts/{artifact_id}',response_model=DocumentArtifactPublic)
def metadata(artifact_id:str,context=Depends(get_context),session=Depends(get_session,scope='function')):
    require_tool_access(context,session)
    return checked_artifact(artifact_id=artifact_id,context=context,session=session).model_dump(mode='json',exclude={'path'})
