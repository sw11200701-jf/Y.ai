"""Local model-test metadata only. Never log identifiers, inputs, or exceptions."""
import json
import logging
import re

_ROUTES = (
    (re.compile(r'/api/v1/models/[^/]+/test-inference\Z'), '/api/v1/models/{id}/test-inference'),
    (re.compile(r'/api/v1/model-providers/[^/]+/test-connection\Z'), '/api/v1/model-providers/{id}/test-connection'),
)
_CODES = frozenset({
    'AUTH_REQUIRED', 'SESSION_REVOKED', 'CSRF_REJECTED', 'HTTPS_REQUIRED', 'PASSWORD_CHANGE_REQUIRED',
    'PERMISSION_DENIED', 'FORBIDDEN', 'ROLE_POLICY_INVALID', 'NOT_FOUND', 'CONNECTION_BLOCKED',
    'MODEL_DISABLED', 'MODEL_CREDENTIAL_MISSING', 'MODEL_VERSION_CHANGED', 'MODEL_NOT_CONFIGURED',
    'MODEL_NOT_AVAILABLE', 'MODEL_ID_AMBIGUOUS', 'MODEL_PARAMETER_UNSUPPORTED', 'MODEL_CAPABILITY_UNAVAILABLE',
    'CONTEXT_BUDGET_EXCEEDED', 'MODEL_TEST_INCOMPLETE', 'MODEL_ERROR', 'MODEL_TIMEOUT', 'TOOL_ERROR',
    'VALIDATION_ERROR', 'INTERNAL_ERROR', 'SERVICE_UNAVAILABLE',
})


def record_model_test_result(method, path, status, code, request_id):
    if method != 'POST':
        return
    route = next((template for pattern, template in _ROUTES if pattern.fullmatch(path)), None)
    if route is None or type(status) is not int or not 100 <= status <= 599:
        return
    record = {'event': 'model_test_result', 'route': route, 'status': status,
              'code': 'OK' if status < 400 else code if isinstance(code, str) and code in _CODES else 'UNCLASSIFIED_ERROR'}
    if isinstance(request_id, str) and re.fullmatch(r'[0-9a-f]{32}', request_id):
        record['request_id'] = request_id
    logging.getLogger('uvicorn.error').info(json.dumps(record, separators=(',', ':')))
