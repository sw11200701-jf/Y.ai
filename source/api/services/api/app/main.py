import importlib
import logging
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from app.db import init_db, SessionLocal, get_session
from app.errors import AppError,validation_detail,chinese_message
from app.runner.service import scheduler
from app.runner.router import router as runner_router
from app.settings import settings
from app.auth import router as auth_router,bind_request,refresh_request
from app.access import router as access_router
from app.attachments import router as attachments_router
from app.rbac import router as rbac_router
from app.data_access import router as data_access_router
from app.model_catalog.router import router as model_catalog_router

@asynccontextmanager
async def lifespan(app):
    from app.execution.retained_intents import startup_check
    startup_check()
    from app.secrets import validate_master_key_configuration
    validate_master_key_configuration()
    init_db()
    from app.workspace_logs import init_logs
    init_logs()
    from app.knowledge.slicing_doc_conversion import configure as configure_doc_converter
    server_office = os.name != 'nt' and bool(os.environ.get('FINANCE_OFFICE_SOCKET'))
    try:
        converter = None if server_office else configure_doc_converter(settings.data_dir)
    except (AppError, OSError, ValueError) as exc:
        if os.name != "nt":
            raise
        # DOC is an optional local capability. Keep its cache and configuration
        # untouched and let the normal format check reject DOC while unavailable.
        logging.getLogger("uvicorn.error").warning(
            "Optional DOC converter unavailable: %s", getattr(exc, "code", type(exc).__name__)
        )
        converter = None
    if converter is not None:
        from app.knowledge.slicing_parser import register_local_adapters
        register_local_adapters(doc_converter=converter)
    from app.knowledge.slicing_office_conversion import configure as configure_office_converter
    try:
        office = configure_office_converter(settings.data_dir)
    except (AppError, OSError, ValueError, TypeError) as exc:
        if server_office:
            raise
        logging.getLogger("uvicorn.error").warning(
            "Optional Office converter unavailable: %s", getattr(exc, "code", type(exc).__name__)
        )
        office = None
    if office is not None:
        from app.knowledge.slicing_parser import register_local_adapters
        convert_office, office_formats = office
        register_local_adapters(office_converter=convert_office, office_formats=office_formats)
        if server_office:
            def convert_doc(*, content):
                return convert_office(content=content, kind='doc')
            register_local_adapters(doc_converter=convert_doc)
        logging.getLogger("uvicorn.error").info("Office original upload enabled: %s", ",".join(sorted(office_formats)))
    await scheduler.start()
    yield
    await scheduler.stop()

app = FastAPI(title="Y.ai", version="2.0.0", lifespan=lifespan)
from app.machine_auth import RuntimeBodyLimitMiddleware
app.add_middleware(RuntimeBodyLimitMiddleware)
try:
    from app.provenance import get_release_marker
except ModuleNotFoundError as exc:
    if exc.name not in ("app.provenance",):raise
else:
    # Opaque local build marker only; it never participates in business auth.
    app.state.release_marker=get_release_marker()

from app.request_transactions import RequestTransactionMiddleware
app.add_middleware(RequestTransactionMiddleware)

@app.middleware("http")
async def request_id(request: Request, call_next):
    from app.workspace_logs import record_request
    request.state.request_id = uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        request.state.model_test_error_code = "INTERNAL_ERROR"
        await record_request(request, 500)
        raise
    await record_request(request, response.status_code)
    from app.model_catalog.diagnostics import record_model_test_result
    record_model_test_result(request.method, request.url.path, response.status_code,
                             getattr(request.state, 'model_test_error_code', None), request.state.request_id)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"]="no-store"
    response.headers["Vary"]="Authorization, X-External-User" if request.url.path.startswith(("/api/open/v1","/api/platform/v1")) else "Cookie"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Cross-Origin-Resource-Policy"]="same-origin"
    response.headers["Referrer-Policy"]="no-referrer"
    return response

@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    request.state.model_test_error_code = exc.code
    headers=__import__("app.machine_auth",fromlist=["error_headers"]).error_headers(request,exc)
    if exc.code == "LOGIN_RATE_LIMIT" and isinstance(exc.details, dict) and "retry_after" in exc.details:
        headers["Retry-After"] = str(exc.details["retry_after"])
    return JSONResponse(status_code=exc.status_code, content={"error": exc.as_dict(), "request_id": request.state.request_id}, headers=headers)

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    request.state.model_test_error_code = "VALIDATION_ERROR"
    # Do not echo raw request input: it may contain credentials.
    details = [validation_detail(e) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": chinese_message("VALIDATION_ERROR"), "details": details}, "request_id": request.state.request_id})

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if getattr(request.state,'application_body_too_large',False):
        return JSONResponse(status_code=413,content={'error':{'code':'APP_REQUEST_TOO_LARGE','message':'应用请求超过大小限制','details':None},'request_id':request.state.request_id})
    code = {404:"NOT_FOUND",405:"METHOD_NOT_ALLOWED",401:"AUTH_REQUIRED",403:"FORBIDDEN",413:"PAYLOAD_TOO_LARGE",429:"LOGIN_RATE_LIMIT"}.get(exc.status_code,"VALIDATION_ERROR")
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": chinese_message(code), "details": None}, "request_id": request.state.request_id}, headers=exc.headers)

@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": chinese_message("INTERNAL_ERROR"), "details": None}, "request_id": request.state.request_id})

loaded_modules = []
app.include_router(auth_router,prefix="/api/v1")
from app.asset_archive import router as asset_archive_router
app.include_router(asset_archive_router,prefix="/api/v1")
from app.workspace_logs import router as workspace_logs_router
app.include_router(workspace_logs_router,prefix="/api/v1")
from app.tool_egress import router as tool_egress_router
app.include_router(tool_egress_router,prefix="/api/v1")
from app.user_variables import router as user_variable_router
app.include_router(user_variable_router,prefix="/api/v1")
app.include_router(rbac_router,prefix="/api/v1")
app.include_router(data_access_router,prefix="/api/v1")
app.include_router(access_router,prefix="/api/v1")
app.include_router(attachments_router,prefix="/api/v1")
app.include_router(model_catalog_router,prefix="/api/v1")
for name in ("plugins", "connections", "agents", "finance", "imports", "reporting", "knowledge", "database"):
    try:
        module = importlib.import_module(f"app.{name}.router")
    except ModuleNotFoundError as exc:
        if exc.name not in (f"app.{name}", f"app.{name}.router"):
            raise
        continue
    included=module.router
    if name=="imports":
        try:governed=importlib.util.find_spec("app.knowledge.router") is not None
        except ModuleNotFoundError:governed=False
        if governed:
            # FastAPI defers nested include_router expansion. Build explicit wrappers
            # from the two public module routers; do not mutate their owned objects.
            document_module=importlib.import_module("app.imports.document_router")
            documents=APIRouter(routes=[route for route in document_module.router.routes if getattr(route,"path",None)!="/knowledge/search"])
            included=APIRouter(routes=[route for route in module.router.routes if getattr(route,"path",None) is not None])
            included.include_router(documents)
    app.include_router(included, prefix="/api/v1")
    loaded_modules.append(name)
from app.workflow_file_router import router as workflow_file_router
app.include_router(workflow_file_router, prefix="/api/v1")
from app.execution.curl_api import router as curl_router
app.include_router(curl_router, prefix="/api/v1")
from app.execution.resource_catalog import router as resource_catalog_router
app.include_router(resource_catalog_router, prefix="/api/v1")
app.include_router(runner_router, prefix="/api/v1")
if settings.application_api_enabled:
    from app.application_runtime import configure as configure_application_runtime
    configure_application_runtime()
    from app.external_api.management_router import router as management_router
    from app.external_api.runtime_router import router as runtime_router
    app.include_router(management_router,prefix="/api/v1")
    app.include_router(runtime_router,prefix="/api/open/v1")

@app.get("/api/v1/capabilities")
def capabilities(request: Request, session=Depends(get_session)):
    from app.runner.registry import node_registry
    registry=node_registry()
    from app.auth import identity
    from app.rbac import RUN_PERMISSIONS
    from app.model_catalog.service import usable_catalog
    context=identity(request)
    items=usable_catalog(session,context)['items'] if set(context.permissions).intersection(RUN_PERMISSIONS) else []
    model_configuration={'configured':bool(items),'configuration_ids':[item['id'] for item in items],
        'verified':False,'default_mode':'model','catalog_url':'/api/v1/model-catalog',
        'message':'已配置可用模型，推理验证状态请在模型管理中检查' if items else '尚未配置获准使用的模型'}
    return {"application_api":{"enabled":settings.application_api_enabled},"platform_api":{"enabled":True,"token_url":"/api/platform/v1/token"},"contract_version":"2.0.0",
        "compatible_contracts":["1.0.1"],"workflow_schema_versions":["1.0","2.0"],"node_registry_url":"/api/v1/workflows/node-registry",
        "node_status":{item["type"]:item["status"] for item in registry["items"]},"mode":"model","mode_label":"真实模型",
        "entities":[{"id":identifier,"name":identifier} for identifier in context.allowed_entities],"periods":[],"modules":loaded_modules,
        "tool_allowed_origins":list(settings.tool_allowed_origins),"storage":"sqlite" if settings.database_url.startswith("sqlite") else "postgresql-unverified",
        "runner":{"concurrency":settings.runner_concurrency,"queue_limit":settings.runner_queue_limit,"timeout_seconds":settings.run_timeout_seconds},
        "model_configured":model_configuration["configured"],"model_configuration":model_configuration}

@app.get("/healthz")
async def health():
    from app.local_preview import local_preview_identity
    result = {"status": "ok"}
    from app.model_catalog.public_resolver import POLICY
    result['model_public_resolver']=POLICY.public()
    local = local_preview_identity()
    if local is not None:
        result["local_preview"] = local
    return result

if settings.application_api_enabled:
    original_openapi=app.openapi
    def application_openapi():
        schema=original_openapi()
        schema.setdefault('components',{}).setdefault('securitySchemes',{})['ApplicationKey']={'type':'http','scheme':'bearer','bearerFormat':'ApplicationKey'}
        for path,operations in schema.get('paths',{}).items():
            if not path.startswith('/api/open/v1/'):continue
            for method,operation in operations.items():
                if method not in {'get','post','put','patch','delete'}:continue
                operation['security']=[{'ApplicationKey':[]}]
                existing={value['name'].lower() for value in operation.get('parameters',[])}
                for name,maximum in [('X-Application-Version',100),('X-External-User',128)]:
                    if name.lower() not in existing:operation.setdefault('parameters',[]).append({'name':name,'in':'header','required':False,'schema':{'type':'string','maxLength':maximum}})
                if path.endswith('/runs') or path.endswith('/chat/messages'):
                    for parameter in operation.get('parameters',[]):
                        if parameter['name'].lower()=='idempotency-key':parameter['required']=True
        return schema
    app.openapi=application_openapi


from app.execution.builtin_agents import router as builtin_agents_router
app.include_router(builtin_agents_router,prefix="/api/v1")
from app.execution.semantic_jobs import install_finalizer
install_finalizer()

from app.session_policy import router as session_policy_router
app.include_router(session_policy_router,prefix="/api/v1")

from app.platform_api import manage as platform_management_router,runtime as platform_runtime_router
from app import platform_publications, platform_resources, platform_space_roles, platform_files, platform_knowledge, platform_database, platform_contract
app.include_router(platform_management_router,prefix="/api/v1")
app.include_router(platform_runtime_router,prefix="/api/platform/v1")
from app.resource_policy import router as resource_policy_router
app.include_router(resource_policy_router,prefix="/api/v1")
