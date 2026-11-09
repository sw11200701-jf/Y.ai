"""Pure model requirements for a trusted bridge preflight, never a send receipt."""
from app.errors import AppError
from .model_adapter import load_config, effective_parameters
from .prompts import normalize_response_format

def preflight_model_requirements(*,model_config_id,expected_version=None,requires_tools=False,
                                 parameters=None,context_budget=None,response_format=None,context,session,document_policy=None):
    from app.execution.effects import get_effect_context
    if get_effect_context() is not None:
        raise AppError('EXECUTION_PREFLIGHT_ONLY','此模型预检仅能在副作用issued之前调用',409)
    if type(requires_tools) is not bool:
        raise AppError('VALIDATION_ERROR','工具需求须由服务器实际定义计算',422)
    config=load_config(model_config_id,context=context,session=session,expected_version=expected_version)
    from app.model_catalog.security import validate_model_address_policy
    validate_model_address_policy(config)
    values=effective_parameters(parameters,config)
    budget=min(8192,config.context_window) if context_budget is None else context_budget
    if type(budget) is not int or not 512<=budget<=config.context_window:
        raise AppError('CONTEXT_BUDGET_EXCEEDED','所选模型上下文预算无效',422)
    if requires_tools and not config.capabilities.get('tools'):
        raise AppError('MODEL_CAPABILITY_UNAVAILABLE','所选模型未声明支持本轮实际工具调用',422)
    from .reasoning_profiles import request_options
    request_options(config,tools=[{}] if requires_tools else None,parameters=values)
    from .reasoning_profiles import document_request_options
    document_request_options(config,document_policy)
    output=normalize_response_format(response_format)
    if output and output['type']!='text' and not config.capabilities.get(output['type']):
        raise AppError('MODEL_CAPABILITY_UNAVAILABLE','所选模型未声明本轮结构化输出能力',422)
    # No HTTP, secret lookup, model invocation, mutable state or provider JSON.
    # Only01's bridge, having called this before its own durable intent, may
    # classify a thrown failure as effect_issued=false. This dict is not proof.
    return {'configuration_id':model_config_id,'config_version':config.config_version,
            'requires_tools':requires_tools,'output_format':output['type'] if output else 'text',
            'max_output_tokens':values['max_tokens'],'context_budget':budget}
