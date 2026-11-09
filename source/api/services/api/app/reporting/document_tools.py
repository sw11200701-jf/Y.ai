"""Approved builtin renderer metadata and a server-only durable render entry."""
from app.auth import validate_context
from app.db import get_resource
from app.errors import AppError
from .document_specs import TOOLS, validate_content
from .document_renderers import render_document, MIME


def document_tool_catalog():
    descriptions = {'document.word.v1': ('生成Word文档', '按章节、正文、列表和表格生成通用DOCX文档。'),
        'document.excel.v1': ('生成Excel工作簿', '按有类型的列和数据行生成通用XLSX工作簿，不执行公式。'),
        'document.powerpoint.v1': ('生成PowerPoint', '按分页列表或可编辑表格生成真实PPTX，内容过密须拆页。')}
    artifact_schema = {'type': 'object', 'properties': {'artifact_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1,
        'items': {'type': 'string'}}}, 'required': ['artifact_ids'], 'additionalProperties': False}
    return {'items': [{'id': key, 'version': 1, 'name': descriptions[key][0], 'description': descriptions[key][1],
        'call_method': 'approved_builtin', 'fixed_connection': None, 'network_access': False,
        'input_schema': schema.model_json_schema(), 'output_schema_after_central_registration': artifact_schema,
        'renderer_entry': 'app.reporting.document_tools.render_for_execution', 'renderer_available': True,
        'execution_available': False, 'unavailable_reason': 'CENTRAL_ARTIFACT_REGISTRATION_NOT_CONNECTED',
        'limits': {'input_bytes': 262144, 'text_characters': 64000, 'output_bytes': 2097152,
                   'max_spreadsheet_cells': 10000, 'max_slides': 20},
        'required_source_action': 'export_detail', 'requires_durable_effect': True,
        'timeout_policy': 'central_remaining_deadline', 'download_registration_required': True}
        for key, schema in TOOLS.items()]}


def render_for_execution(*, tool_id, arguments, context, session):
    """Return private bytes, never a fabricated public Artifact ID/download URL.

    Central integration must call this in an admitted step and atomically register
    its result with actual run/source provenance before returning artifact_ids.
    """
    from app.execution.effects import get_effect_context
    from app.execution.storage import check_active_run
    from app.rbac import require_any_permission, RUN_PERMISSIONS
    from app.data_access import authorize_data_resource
    validate_context(context, session)
    require_any_permission(context, session, RUN_PERMISSIONS)
    effect = get_effect_context()
    if effect is None or (effect.principal_id, effect.tenant_id, effect.workspace_id) != (
            context.operator_id, context.tenant_id, context.workspace_id):
        raise AppError('DOCUMENT_EFFECT_REQUIRED', '文档生成需要当前调用者的受控持久执行步骤', 409)
    check_active_run(session, effect.run_id)
    run = get_resource(session, 'run', effect.run_id, context)
    sources = run.get('data_sources', [])
    for source in sources:
        authorize_data_resource(session=session, context=context, kind=source['kind'],
            resource_id=source['resource_id'], action='export_detail')
    validate_content(tool_id, arguments)
    result = render_document(tool_id=tool_id, arguments=arguments)
    # Expire stale current-context decisions and fence cancellation/revocation
    # again after CPU work. The central registry must recheck before commit too.
    validate_context(context, session)
    check_active_run(session, effect.run_id)
    get_resource(session, 'run', effect.run_id, context)
    for source in sources:
        authorize_data_resource(session=session, context=context, kind=source['kind'],
            resource_id=source['resource_id'], action='export_detail')
    return result
