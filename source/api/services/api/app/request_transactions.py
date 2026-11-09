"""Request DB phases must not block the ASGI loop or queue behind handlers.

The admission bound covers bind -> handler -> commit/rollback -> close. Its
dedicated thread limiter has one token per admitted request, so other requests
waiting for SQLite cannot consume the holder's commit/rollback execution slot.
Only one DB phase per request runs at once; no Session is shared with jobs.
"""
import anyio
from sqlalchemy.exc import OperationalError
from fastapi.responses import JSONResponse
from starlette.requests import Request
from app.db import SessionLocal
from app.auth import bind_request,refresh_request
from app.errors import AppError

MAX_REQUEST_TRANSACTIONS=16
_admission=None
_db_threads=None

def _error(request,exc):
    from app.machine_auth import error_headers
    request.state.model_test_error_code=exc.code
    return JSONResponse(status_code=exc.status_code,
        content={'error':exc.as_dict(),'request_id':getattr(request.state,'request_id',None)},
        headers=error_headers(request,exc))

def _busy(request,*,accepted=False):
    if accepted:
        # A model/queue may already have been contacted. Do not suggest a
        # blind retry or change its durable ledger to a guessed outcome.
        return _error(request,AppError('REQUEST_OUTCOME_UNCONFIRMED',
            '本次请求结果未能确认，请核对运行记录，勿重复提交',503,
            {'failure_stage':'request_transaction','request_accepted':True,
             'outcome_unknown':True,'retry_safe':False,'request_sent':None,
             'response_received':None,'response_valid':None}))
    response=_error(request,AppError('REQUEST_BUSY','当前请求较多，请稍后重试',503,
        {'failure_stage':'request_admission','request_accepted':False,
         'retry_safe':True,'request_sent':False,'response_received':False,
         'response_valid':False}))
    response.headers['Retry-After']='1'
    return response

def _bind(request,session):
    from app.machine_auth import is_service
    bind_request(request,session)
    if is_service(getattr(request.state,'business_context',None)):
        session.commit()

def _finish(request,session,success):
    if success:
        refresh_request(request,session)
        session.commit()
    else:
        session.rollback()

def _sqlite_busy(exc):
    # Do not turn unrelated database failures into a retry suggestion.
    import sqlite3
    original=exc.orig
    return isinstance(original,sqlite3.OperationalError) and (
        getattr(original,'sqlite_errorcode',None) in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}
        or str(original) in {'database is locked','database table is locked'})

class RequestTransactionMiddleware:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        global _admission,_db_threads
        from app.machine_auth import prepare_open_request,sanitize_credential_url
        request=Request(scope,receive=receive)
        admitted=False;session=None;closed=False;started=False;borrower=object()
        async def db(function,*args):
            # Copy current ContextVars; never abandon an in-flight DB phase.
            with anyio.CancelScope(shield=True):
                return await anyio.to_thread.run_sync(function,*args,limiter=_db_threads,abandon_on_cancel=False)
        async def close():
            nonlocal closed,admitted
            if session is not None and not closed:
                await db(session.close)
                closed=True;request.state.db_session=None
            if admitted:
                _admission.release_on_behalf_of(borrower);admitted=False
        try:
            sanitize_credential_url(request)
            if request.url.path=='/healthz' and request.method=='GET':
                if request.headers.get('authorization'):
                    raise AppError('AUTH_SCHEME_NOT_ALLOWED','控制台只接受人类会话，不接受应用Bearer凭据',401)
                return await self.app(scope,receive,send)
            if _admission is None:
                _admission=anyio.CapacityLimiter(MAX_REQUEST_TRANSACTIONS)
                _db_threads=anyio.CapacityLimiter(MAX_REQUEST_TRANSACTIONS)
            try:_admission.acquire_on_behalf_of_nowait(borrower)
            except anyio.WouldBlock:return await _busy(request)(scope,receive,send)
            admitted=True
            session=await db(SessionLocal)
            request.state.db_session=session
            await prepare_open_request(request)
            await db(_bind,request,session)
            downstream_receive=receive
            if hasattr(request,'_body'):
                # prepare_open_request has consumed bounded JSON for the user
                # alias. Replay those exact bytes once, without buffering files.
                body_sent=False
                async def replay_receive():
                    nonlocal body_sent
                    if not body_sent:
                        body_sent=True
                        return {'type':'http.request','body':request._body,'more_body':False}
                    return await receive()
                downstream_receive=replay_receive
            async def transaction_send(message):
                nonlocal started
                if message['type']=='http.response.start':
                    # Observe cancellation queued while a synchronous handler
                    # was shielded in its worker, before starting a commit.
                    await anyio.lowlevel.checkpoint()
                    # The actual ASGI response boundary, before any stream body
                    # executes. No request Session survives into that body.
                    await db(_finish,request,session,message['status']<400)
                    await close()
                    started=True
                await send(message)
            try:
                await self.app(scope,downstream_receive,transaction_send)
            except BaseException:
                if not closed:await db(session.rollback)
                raise
        except AppError as exc:
            if started:raise
            if session is not None and not closed:await db(session.rollback)
            await close()
            await _error(request,exc)(scope,receive,send)
        except OperationalError as exc:
            if started or not _sqlite_busy(exc):raise
            if session is not None and not closed:await db(session.rollback)
            await close()
            await _busy(request,accepted=True)(scope,receive,send)
        finally:
            await close()
