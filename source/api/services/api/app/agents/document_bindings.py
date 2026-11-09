"""Explicit fixed builtin document tools for simple model Agents only."""
from app.errors import AppError
from app.auth import validate_context
from app.rbac import require_any_permission,RUN_PERMISSIONS
from app.reporting.document_specs import TOOLS

FUNCTIONS={'generate_word_document':'document.word.v1','generate_excel_workbook':'document.excel.v1','generate_powerpoint':'document.powerpoint.v1'}


def authorize_config(config,*,context,session):
    selected=config.get('document_tools',[])
    if not selected:return []
    validate_context(context,session);require_any_permission(context,session,RUN_PERMISSIONS)
    if (not isinstance(selected,list) or len(selected)>3 or len(set(selected))!=len(selected)
            or any(item not in TOOLS for item in selected)):
        raise AppError('DOCUMENT_TOOL_UNSUPPORTED','文档工具必须是服务器批准的固定版本且不能重复',422)
    if config.get('agent_type','simple')!='simple' or config.get('main_workflow_id') or config.get('planner_mode')!='model':
        raise AppError('DOCUMENT_AGENT_UNSUPPORTED','通用文档工具当前仅支持简单模型智能体',422)
    return selected


def tool_definitions(config):
    selected=config.get('document_tools',[])
    descriptions={'document.word.v1':'按已授权内容生成真正Word文档并返回受控文件引用；无脚本/网络/自选路径。',
        'document.excel.v1':'按类型明确的工作表数据生成真正Excel，不执行公式，返回受控文件引用。',
        'document.powerpoint.v1':'生成真正可编辑PPTX；每页列表或表格，密集内容须拆页，返回受控文件引用。'}
    return [{'type':'function','function':{'name':name,'description':descriptions[identifier],
        'parameters':TOOLS[identifier].model_json_schema()}} for name,identifier in FUNCTIONS.items() if identifier in selected]


CAPABILITIES={'document_tools':{'supported':True,'max_items':3,'execution_kinds':['simple_model_agent'],
    'fixed_tool_ids':list(TOOLS),'function_names':FUNCTIONS,'artifact_contract_version':'generated-document-artifact-v1',
    'uploaded_scripts_supported':False,'network_access':False,'source_action':'export_detail'}}
