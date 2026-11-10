"""Mount the workbench with the same write serialization as the platform API."""
from fastapi import APIRouter, Depends, Request

from app.db import get_session
from app.knowledge.database_router import router as workbench
from app.platform_api import write_lock


def write_transaction(request: Request, session=Depends(get_session)):
    if request.method in {"POST", "PATCH"} and not request.url.path.endswith(("/query", "/export")):
        if session.get_bind().dialect.name == "sqlite":
            write_lock(session)


router = APIRouter()
router.include_router(workbench, dependencies=[Depends(write_transaction)])
