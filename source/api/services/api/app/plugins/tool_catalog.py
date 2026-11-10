"""Caller-filtered visual metadata for existing published HTTP tools."""
from copy import deepcopy
from app.contracts import ToolBinding
from app.db import list_resources
from app.errors import AppError
from .permissions import require_tool_access
from .service import resolve_binding, execution_context


def published_tool_catalog(*, context, session):
    require_tool_access(context, session)
    items = []
    for version in list_resources(session, 'plugin_version', context):
        if version.get('status') != 'published' or not version.get('default_connection_id'):
            continue
        for tool in version.get('tools', []):
            binding = ToolBinding(plugin_version_id=version['id'], tool_id=tool['id'],
                                  connection_id=version['default_connection_id'])
            raw_binding = binding.model_dump(include={'plugin_version_id', 'tool_id', 'connection_id'})
            scoped = getattr(context, 'allowed_bindings', None)
            if scoped is not None and raw_binding not in scoped:
                continue
            try:
                resolved = resolve_binding(binding, context, session)
            except AppError as exc:
                if exc.status_code in {403, 404, 409, 422}:
                    continue
                raise
            endpoint = resolved['endpoint']
            policy = execution_context(context, session)
            items.append({'binding': raw_binding, 'version': version['version'], 'name': tool['name'],
                'description': tool['description'], 'call_method': 'published_http',
                'http_method': endpoint['method'], 'request_encoding': endpoint.get('request_encoding', 'json'),
                'read_only': endpoint['read_only'], 'input_schema': deepcopy(endpoint['input_schema']),
                'output_schema': deepcopy(endpoint['output_schema']),
                'input_fields': deepcopy(endpoint.get('input_fields')), 'output_fields': deepcopy(endpoint.get('output_fields')),
                'fixed_connection': {'id': resolved['connection']['id'], 'name': resolved['connection']['name']},
                'timeout_seconds_effective': min(float(policy['tool_timeout_seconds']), 30),
                'max_output_bytes': policy['max_output_bytes'],
                'required_permissions_any': ['page.plugins', 'page.ai', 'page.workbench'],
                'resource_access': ['current_published_version_read', 'current_connection_read', 'current_binding_scope'],
                'runtime_entry': 'app.plugins.service.execute_tool', 'test_endpoint': '/api/v1/tools/test',
                'network_request_sent': False})
    return {'items': items, 'metadata_only': True, 'unconfigured_tools_included': False}
