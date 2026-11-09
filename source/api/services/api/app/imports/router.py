from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.contracts import BusinessContext
from app.db import get_session, list_resources
from app.context import get_context
from .service import import_file, commit_batch, public_batch
from .workbook import template_bytes, MAX_BYTES
from .document_router import router as document_router

router = APIRouter(tags=["imports"])
router.include_router(document_router)

@router.get("/imports/template")
def template():
    return Response(template_bytes(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="synthetic-finance-template.xlsx"'})

@router.get("/imports")
def imports(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return {"items": [public_batch(b) for b in list_resources(session, "import_batch", context)]}

@router.post("/imports/preview")
async def preview(file: UploadFile = File(...), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    content = await file.read(MAX_BYTES + 1)
    result = await import_file(filename=file.filename or "upload.xlsx", content=content, session=session, context=context)
    session.commit()
    return result

@router.post("/imports/{batch_id}/commit")
async def commit(batch_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    result = await commit_batch(batch_id=batch_id, session=session, context=context)
    session.commit()
    return result
