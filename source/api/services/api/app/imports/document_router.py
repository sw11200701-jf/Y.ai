from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.context import get_context
from app.contracts import BusinessContext
from app.db import get_session
from app.knowledge.data_store import get_resource, list_resources
from app.errors import AppError
from .documents import import_document, extract_document, search_documents, public_document, capabilities, validate_file_id, MAX_BYTES, document_metadata

router = APIRouter(tags=["documents"])

class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_size: int = Field(default=1000, ge=200, le=2000)
    chunk_overlap: int = Field(default=100, ge=0, le=200)

class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    file_ids: list[str] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)

@router.get("/documents/capabilities")
def document_capabilities():
    return capabilities()

@router.get("/documents")
def documents(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    items = []
    for doc in list_resources(session, "document", context):
        try:
            items.append(document_metadata(file_id=doc["id"], context=context, session=session))
        except AppError as exc:
            if exc.status_code != 404:
                raise
    return {"items": items}

@router.post("/documents/import")
async def upload_document(file: UploadFile = File(...), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    result = await import_document(filename=file.filename or "upload.txt", content=await file.read(MAX_BYTES+1), context=context, session=session)
    session.commit()
    return result

@router.get("/documents/{file_id}")
def document_detail(file_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    validate_file_id(file_id)
    return document_metadata(file_id=file_id, context=context, session=session)

@router.post("/documents/{file_id}/extract")
async def extract(file_id: str, body: ExtractRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    result = await extract_document(file_id=file_id, context=context, session=session, **body.model_dump())
    session.commit()
    return result

@router.post("/documents/search")
async def search(body: SearchRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await search_documents(context=context, session=session, **body.model_dump())
