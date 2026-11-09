from typing import Any
import re

MESSAGES={
    "AUTH_REQUIRED":"请先登录", "INVALID_CREDENTIALS":"账号或密码不正确", "SESSION_REVOKED":"登录已过期或被撤销，请重新登录",
    "CSRF_REJECTED":"安全校验未通过，请刷新页面后重试", "HTTPS_REQUIRED":"当前服务需要使用HTTPS安全连接",
    "FORBIDDEN":"没有执行此操作的权限", "PERMISSION_DENIED":"没有此功能的访问权限",
    "VALIDATION_ERROR":"提交内容不符合要求，请检查标记字段", "NOT_FOUND":"资源不存在或无权访问",
    "CONFLICT":"资源状态已变化，请刷新后重试", "ALREADY_INITIALIZED":"系统已经初始化，请登录现有账号",
    "LOGIN_RATE_LIMIT":"操作过于频繁，请稍后重试", "INTERNAL_ERROR":"服务暂时无法完成此操作，请稍后重试",
    "SERVICE_UNAVAILABLE":"服务暂未就绪，请稍后重试", "QUEUE_FULL":"运行队列已满，请稍后重试",
    "RUN_TIMEOUT":"运行已超过允许时间", "RESOURCE_LIMIT":"操作超过允许的资源或执行限制",
    "INVALID_WORKFLOW":"工作流配置无效，请检查节点和连线", "INVALID_BINDING":"工具或连接绑定不可用，请重新选择",
    "CONNECTION_BLOCKED":"连接或调用参数不符合访问规则", "TOOL_ERROR":"工具调用未能完成，请检查连接和参数",
    "MODEL_NOT_CONFIGURED":"尚未配置可用模型", "MODEL_CAPABILITY_UNAVAILABLE":"当前模型未配置所需的图像或语音能力",
    "MODEL_TIMEOUT":"模型响应超时，请稍后重试", "MODEL_ERROR":"模型调用失败，请检查模型配置",
    "NODE_UNAVAILABLE":"此节点的执行能力尚未开放", "VARIABLE_NOT_FOUND":"引用的变量不存在或当前分支未产生该变量",
    "VARIABLE_TYPE_MISMATCH":"变量类型不符合节点要求", "MESSAGE_INPUT_CONFLICT":"消息参数与运行参数冲突",
    "MESSAGE_INVALID":"消息类型或内容不符合要求", "TRIGGER_NOT_MATCHED":"没有匹配此消息类型的触发入口",
    "ATTACHMENT_INVALID":"附件内容、格式或大小不符合要求", "OUTPUT_SCHEMA_MISMATCH":"返回数据不符合配置的输出字段要求",
    "AGENT_ROUND_LIMIT":"智能体已达到允许的执行轮数", "AGENT_TIMEOUT":"智能体执行超时",
    "CONTEXT_BUDGET_EXCEEDED":"输入内容超过允许的上下文长度", "ITERATION_LIMIT":"循环次数已达到上限",
    "NODE_OUTPUT_LIMIT":"节点输出超过允许大小", "NOT_PUBLISHED":"资源尚未发布，请先发布可用版本",
    "DATASET_INVALID":"数据集或版本快照不一致，请重新生成", "DATASET_LIMIT":"数据集超过允许大小",
    "PASSWORD_CHANGE_REQUIRED":"请先修改初始密码",
    "METHOD_NOT_ALLOWED":"此接口不支持当前操作方式", "PAYLOAD_TOO_LARGE":"提交内容超过允许大小",
}

def register_error_messages(messages):
    """Trusted module registration only. Do not register user/provider messages."""
    for code,message in messages.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}",code) or not re.search(r"[\u3400-\u9fff]",message):raise ValueError("错误映射必须使用稳定错误码和中文消息")
        MESSAGES[code]=message

def chinese_message(code,message=None):
    if isinstance(message,str) and re.search(r"[\u3400-\u9fff]",message):return message
    return MESSAGES.get(code,"请求未能完成，请检查操作后重试")

def safe_details(value):
    if isinstance(value,dict):
        private={"input","password","newpassword","currentpassword","initialpassword","temporarypassword","temppassword","passwordhash","token","idtoken","accesstoken","refreshtoken","bearertoken","csrftoken","xcsrftoken","secret","secretref","privatekey","authorization","cookie","setcookie","credential","credentialref","apikey","sessionid","traceback","stack"}
        return {key:safe_details(item) for key,item in value.items() if isinstance(key,str) and re.sub(r"[^a-z0-9]","",key.lower()) not in private}
    if isinstance(value,(list,tuple)):return [safe_details(item) for item in value]
    if isinstance(value,(str,int,float,bool)) or value is None:return value
    return None

def validation_detail(error):
    kind=error["type"];context=error.get("ctx",{})
    messages={"missing":"此字段为必填项","extra_forbidden":"不支持此字段","string_type":"请输入文本","int_type":"请输入整数","int_parsing":"请输入有效整数","bool_type":"请选择是或否","bool_parsing":"请选择有效的布尔值","list_type":"此字段需要列表","dict_type":"此字段需要对象","literal_error":"请选择允许的选项","enum":"请选择允许的选项","string_pattern_mismatch":"内容格式不符合要求","json_invalid":"内容不是有效的JSON","value_error":"字段内容不符合要求"}
    if kind in ("string_too_short","too_short"):message=f"长度不能少于{context.get('min_length','要求的')}个字符或项目"
    elif kind in ("string_too_long","too_long"):message=f"长度不能超过{context.get('max_length','允许的')}个字符或项目"
    elif kind.startswith(("greater_than","less_than")):message="数值超出允许范围"
    else:message=messages.get(kind,"字段内容或类型不符合要求")
    bounds={key:value for key,value in context.items() if key in {"min_length","max_length","gt","ge","lt","le"} and isinstance(value,(int,float))}
    return {"loc":list(error["loc"]),"type":kind,"message":message,**({"context":bounds} if bounds else {})}

class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.status_code, self.details = code, message, status_code, details

    def as_dict(self):
        return {"code": self.code, "message": chinese_message(self.code,self.message), "details": safe_details(self.details)}
