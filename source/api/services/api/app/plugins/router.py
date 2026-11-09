from fastapi import APIRouter, Depends, Query
from threading import RLock
from pydantic import Field
from sqlalchemy.orm import Session

from app.context import get_context
from app.contracts import BusinessContext, ToolBinding
from app.db import get_resource, get_session, list_resources
from .service import archive_plugin, create_plugin, edit_member, list_plugins, materialize_binding, update_plugin, publish_plugin, references, execute_tool, test_draft, version_view
from .specs import BindingSelection, EndpointDraft, PluginDraft, StrictModel, ToolDraft
from .fields import FieldDefinition
from app.errors import AppError
from pydantic import ValidationError
from typing import Literal
from .validation import ValidationRoute, details as validation_details

router = APIRouter(tags=["plugins"], route_class=ValidationRoute)
_publish_lock = RLock()  # Demo runs one API worker; serialize version-number allocation.


def test_response(result):
    # REST compatibility only: never add aliases to canonical runner serialization.
    return {**result.model_dump(mode="json"), "status": result.audit["status"],
            "output_values": result.data, "response_preview": getattr(result, "_response_preview", result.data)}


class ToolTest(StrictModel):
    binding: BindingSelection
    arguments: dict = Field(default_factory=dict)


class DraftTest(StrictModel):
    endpoint_id: str
    connection_id: str | None = None
    arguments: dict = Field(default_factory=dict)


@router.get('/tools/catalog')
def catalog(session: Session = Depends(get_session, scope='function'), context: BusinessContext = Depends(get_context)):
    from .tool_catalog import published_tool_catalog
    return published_tool_catalog(context=context, session=session)


@router.get("/plugins")
def plugins(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=200),
            status: str = Query("", pattern="^(|draft|published|archived)$"),
            connection_id: str = "",
            session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return list_plugins(context=context, session=session, page=page, page_size=page_size, q=q, status=status, connection_id=connection_id)


@router.post("/plugins", status_code=201)
def create(data: PluginDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return create_plugin(data, context, session)


@router.get("/plugins/{plugin_id}")
def get(plugin_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return get_resource(session, "plugin", plugin_id, context)


@router.put("/plugins/{plugin_id}")
def update(plugin_id: str, data: PluginDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return update_plugin(plugin_id, data, context, session)


@router.post("/plugins/{plugin_id}/publish", status_code=201)
def publish(plugin_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    with _publish_lock:
        result = publish_plugin(plugin_id, context, session)
        session.commit()
        return result


@router.get("/plugins/{plugin_id}/versions")
def versions(plugin_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    get_resource(session, "plugin", plugin_id, context)
    return {"items": sorted([version_view(v) for v in list_resources(session, "plugin_version", context) if v["plugin_id"] == plugin_id], key=lambda v: v["version"])}


@router.get("/plugins/{plugin_id}/references")
def refs(plugin_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return {"items": references(plugin_id, context, session)}


@router.post("/tools/test")
async def test(data: ToolTest, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    binding = materialize_binding(data.binding, context, session)
    result = await execute_tool(binding=binding, arguments=data.arguments, context=context, session=session)
    return test_response(result)


@router.post("/tools/bindings/resolve")
def resolve_tool(data: BindingSelection, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return materialize_binding(data, context, session)


class CompileFields(StrictModel):
    method: Literal["GET", "POST"] = "GET"
    path: str
    input_fields: list[FieldDefinition] = Field(default_factory=list)
    output_fields: list[FieldDefinition] = Field(default_factory=list)


@router.post("/plugins/fields/compile")
def preview_fields(data: CompileFields, context: BusinessContext = Depends(get_context)):
    try:
        endpoint = EndpointDraft.model_validate({"id": "preview", "name": "字段预览", **data.model_dump()})
        return endpoint.model_dump()
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "字段定义、路径参数或嵌套类型配置无效", 422,
                       details=validation_details(exc.errors())) from exc


@router.post("/plugins/{plugin_id}/test")
async def test_endpoint(plugin_id: str, data: DraftTest, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    result = await test_draft(plugin_id=plugin_id, endpoint_id=data.endpoint_id, connection_id=data.connection_id,
                              arguments=data.arguments, context=context, session=session)
    return test_response(result)


@router.delete("/plugins/{plugin_id}")
def archive(plugin_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return archive_plugin(plugin_id, context, session)


@router.post("/plugins/{plugin_id}/endpoints", status_code=201)
def add_endpoint(plugin_id: str, data: EndpointDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="endpoints", member_id=None, value=data, context=context, session=session)


@router.put("/plugins/{plugin_id}/endpoints/{endpoint_id}")
def update_endpoint(plugin_id: str, endpoint_id: str, data: EndpointDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="endpoints", member_id=endpoint_id, value=data, context=context, session=session)


@router.delete("/plugins/{plugin_id}/endpoints/{endpoint_id}")
def remove_endpoint(plugin_id: str, endpoint_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="endpoints", member_id=endpoint_id, value=None, context=context, session=session)


@router.post("/plugins/{plugin_id}/tools", status_code=201)
def add_tool(plugin_id: str, data: ToolDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="tools", member_id=None, value=data, context=context, session=session)


@router.put("/plugins/{plugin_id}/tools/{tool_id}")
def update_tool(plugin_id: str, tool_id: str, data: ToolDraft, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="tools", member_id=tool_id, value=data, context=context, session=session)


@router.delete("/plugins/{plugin_id}/tools/{tool_id}")
def remove_tool(plugin_id: str, tool_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return edit_member(plugin_id=plugin_id, collection="tools", member_id=tool_id, value=None, context=context, session=session)
