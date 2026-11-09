"""Secret-input HTTP adapter for03's one preview/confirmation protocol."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, SecretStr, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool
import asyncio
from app.context import get_context
from app.contracts import BusinessContext
from app.db import get_session, SessionLocal
from app.errors import AppError
from app.plugins.curl_import import CurlConfirmation, preview_curl, confirm_curl
from app.plugins.permissions import require_page

router = APIRouter(tags=["plugins"])


class CurlPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: SecretStr = Field(min_length=1, max_length=32768)

    @field_validator("command")
    @classmethod
    def bounded_command(cls, value):
        if len(value.get_secret_value().encode("utf-8"))>32768:
            raise ValueError("cURL文本超过32KiB限制")
        return value


class CurlConfirmRequest(CurlPreviewRequest):
    selection: CurlConfirmation


@router.post("/plugins/curl-preview")
def preview(body: CurlPreviewRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    require_page(context, session, "page.plugins")
    return preview_curl(body.command.get_secret_value())


@router.post("/plugins/curl-confirm")
async def confirm(body: CurlConfirmRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    require_page(context, session, "page.plugins")
    # Keep SQLite waits off the API loop, with a wholly worker-owned Session.
    #03's SAVEPOINT + current revision checks/PG row lock remain authoritative.
    current=context.model_copy(deep=True)
    def persist():
        with SessionLocal() as isolated:
            try:
                result=asyncio.run(confirm_curl(body.command.get_secret_value(), body.selection, context=current, session=isolated))
                isolated.commit()
                return result
            except OperationalError as exc:
                isolated.rollback()
                if getattr(exc.orig, "sqlite_errorcode", 0) & 255 in {5, 6}:
                    raise AppError("CURL_CONFIRM_CONFLICT", "其他确认已占用当前草稿，请刷新预览后确认", 409) from None
                raise
            except BaseException:
                isolated.rollback()
                raise
    return await run_in_threadpool(persist)
