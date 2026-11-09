"""Self-owned node registry. Metadata and runtime both use these definitions."""
import importlib
from copy import deepcopy

ANY = {}
STRING = {"type": "string"}
ARRAY = {"type": "array"}
OBJECT = {"type": "object"}
BOOL = {"type": "boolean"}
OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "empty", "not_empty"]

def obj(properties=None, required=(), additional=False):
    return {"type":"object", "properties":properties or {}, "required":list(required), "additionalProperties":additional}

def enum(*values): return {"enum":list(values)}
def integer(low, high): return {"type":"integer","minimum":low,"maximum":high}

DEFINITIONS = {}
def define(kind, label, category, config=None, inputs=None, outputs=None, ports=None, owner="01", service=None, reason=None):
    DEFINITIONS[kind] = {"type":kind,"label":label,"category":category,"config_schema":config or obj(),"input_schema":inputs or obj(additional=True),"output_schema":outputs or obj(additional=True),"ports":ports or ["default"],"owner":owner,"service":service,"status":"unavailable" if reason else ("needs_configuration" if service or kind in ("plugin","http") else "supported"),"reason":reason or ("Requires published tool binding/connection" if kind in ("plugin","http") else "Requires configured module/model" if service else None)}

for kind,label in (("start","开始"),("input","用户输入")):
    define(kind,label,"control",outputs=obj({"inputs":OBJECT,"query":OBJECT,"prompt":STRING,"message":OBJECT},additional=True))
for kind,label in (("end","结束"),("output","输出"),("answer","回答")):
    define(kind,label,"control",outputs=obj({"values":OBJECT},("values",)),ports=[])
    DEFINITIONS[kind]["ports"] = []
define("if_else","条件分支","control",obj({"operator":enum(*OPS)},("operator",)),obj({"left":ANY,"right":ANY},("left",)),obj({"result":BOOL},("result",)),["true","false"])
# Legacy left/right stays valid. New condition groups are data expressions,
# with bounded recursion evaluated by the same safe comparison operators.
condition_leaf = obj({"operator":enum(*OPS),"left":ANY,"right":ANY},("operator","left"))
condition_group = obj({"logical_operator":enum("and","or"),"conditions":{"type":"array","minItems":1,"maxItems":16,"items":{"$ref":"#/$defs/condition"}}},("logical_operator","conditions"))
DEFINITIONS["if_else"]["config_schema"] = {**obj({"operator":enum(*OPS),"logical_operator":enum("and","or")}),"oneOf":[{"required":["operator"]},{"required":["logical_operator"]}]}
DEFINITIONS["if_else"]["input_schema"] = {**obj({"left":ANY,"right":ANY,"conditions":{"type":"array","minItems":1,"maxItems":16,"items":{"$ref":"#/$defs/condition"}}}),
    "$defs":{"condition":{"oneOf":[condition_leaf,condition_group]}}, "oneOf":[{"required":["left"]},{"required":["conditions"]}]}
define("template","模板转换","transform",obj({"template":STRING},("template",)),outputs=obj({"text":STRING},("text",)))
define("assign","变量赋值","transform",obj({"name":STRING,"operation":enum("set","append","increment")},("name",)),obj({"value":ANY},("value",)),obj({"value":ANY},("value",)))
define("aggregate","变量聚合","transform",obj({"strategy":enum("first_non_null","collect","concat","merge_objects"),"join":enum("any","all")}),obj({"values":ARRAY},("values",)),obj({"value":ANY},("value",)))
define("list","列表操作","transform",obj({"operation":enum("take","slice","sort","unique","filter","length","flatten"),"limit":integer(0,1000),"offset":integer(0,1000),"field":STRING,"operator":enum(*OPS),"descending":BOOL},("operation",)),obj({"items":ARRAY,"value":ANY},("items",)),obj({"items":ARRAY,"count":{"type":"integer"}},("items","count")))
binding = obj({"plugin_version_id":STRING,"tool_id":STRING,"connection_id":STRING},("plugin_version_id","tool_id","connection_id"))
binding["properties"]["connection_id"]={"type":["string","null"]}
binding["required"]=["plugin_version_id","tool_id"]
for kind,label in (("plugin","插件工具"),("http","HTTP接口（受保护连接）")):
    define(kind,label,"tools",obj({"binding":binding,"arguments":OBJECT},("binding",)),obj({"arguments":OBJECT}),obj({"facts":ARRAY,"data":ANY,"source_refs":ARRAY,"audit":OBJECT,"warnings":ARRAY},("facts",)))
    DEFINITIONS[kind]["input_schema"]["properties"]["message"]=OBJECT
define("query","多数据源财务查询","finance",inputs=obj({"query":OBJECT,"sources":ARRAY}),outputs=obj({"facts":ARRAY,"source_refs":ARRAY},("facts",)))
define("calculate","财务指标计算","finance",inputs=obj({"facts":ARRAY,"query":OBJECT},("facts",)),outputs=obj({"dataset":OBJECT,"dataset_id":STRING,"snapshot_id":STRING},("dataset",)))
define("chart","图表","finance",obj({"series":ARRAY}),obj({"dataset":OBJECT},("dataset",)),obj({"chart_spec":OBJECT},("chart_spec",)))
define("report","Word/Excel报告","finance",inputs=obj({"dataset":OBJECT},("dataset",)),outputs=obj({"artifacts":ARRAY,"artifact_ids":ARRAY,"dataset_id":STRING},("artifacts","artifact_ids")))
define("document_generate","通用Word/Excel/PPT生成","tools",
    obj({"tool_id":enum("document.word.v1","document.excel.v1","document.powerpoint.v1")},("tool_id",)),
    obj({"arguments":OBJECT,"source_resources":{"type":"array","maxItems":32,"items":obj({"kind":STRING,"resource_id":STRING},("kind","resource_id"))}},("arguments",)),
    obj({"artifact_ids":{"type":"array","items":STRING},"artifacts":ARRAY,"data_sources":ARRAY},("artifact_ids","artifacts","data_sources")),
    owner="01",service="app.reporting.document_nodes")
DEFINITIONS["document_generate"].update(status="supported",reason="Fixed strict renderer schema; original durable Run/Artifact/export_detail checks; no model request")
for kind in ("query", "calculate", "chart", "report"):
    DEFINITIONS[kind]["output_schema"]["properties"].update(source_refs=ARRAY, warnings=ARRAY, data_sources=ARRAY)
DEFINITIONS["report"]["output_schema"]["properties"]["snapshot_id"] = STRING
repeat_config = {"body":OBJECT,"max_iterations":integer(1,50),"timeout_seconds":integer(1,60)}
define("iteration","列表迭代","control",obj(repeat_config,("body","max_iterations","timeout_seconds")),obj({"items":ARRAY},("items",)),obj({"items":ARRAY,"count":{"type":"integer"}},("items","count")))
define("loop","有界循环","control",obj({**repeat_config,"state":OBJECT,"until":OBJECT},("body","max_iterations","timeout_seconds","until")),obj({"state":OBJECT}),obj({"state":OBJECT,"count":{"type":"integer"}},("state","count")))
for kind,label in (("llm","大语言模型"),("agent","独立智能体"),("parameter_extractor","参数提取"),("question_classifier","问题分类")):
    define(kind,label,"intelligence",obj(additional=True),obj(additional=True),obj(additional=True),["dynamic"] if kind=="question_classifier" else None,owner="03",service="app.agents.nodes")
for kind,label in (("document_extractor","文档提取"),("knowledge_retrieval","知识检索")):
    define(kind,label,"knowledge",obj(additional=True),obj(additional=True),obj(additional=True),owner="04",service="app.finance.nodes")
define("database_query","结构化数据查询","data",obj({"database_id":STRING,"table_version_id":STRING}),obj({"table_id":STRING,"query":OBJECT},("table_id","query")),obj({"rows":ARRAY,"columns":ARRAY,"total":{"type":"integer"},"policy_version":{"type":"integer"},"source_refs":ARRAY},("rows","total","policy_version"),additional=True),owner="04",service="app.database.nodes")

sampling = obj({"temperature":{"type":"number","minimum":0,"maximum":2},
    "top_p":{"type":"number","minimum":0,"maximum":1},"max_tokens":integer(1,393216),
    "frequency_penalty":{"type":"number","minimum":-2,"maximum":2},
    "presence_penalty":{"type":"number","minimum":-2,"maximum":2},
    "stop":{"type":"array","maxItems":4,"items":{"type":"string","minLength":1,"maxLength":200}},
    "seed":integer(-2147483648,2147483647)})
prompt_messages = {"type":"array","minItems":1,"maxItems":16,"items":obj({
    "role":enum("system","user","assistant"),"content":{"type":"string","minLength":1,"maxLength":16000}},("role","content"))}
response_format = obj({"type":enum("text","json_object","json_schema"),"schema":OBJECT,"name":STRING,"strict":BOOL},("type",))
DEFINITIONS["llm"].update(config_schema=obj({"model_config_id":STRING,"model_config_version":STRING,
    "system_prompt":{"type":"string","maxLength":16000},"prompt_messages":prompt_messages,"model_parameters":sampling,
    "context_budget":integer(512,1048576),"response_format":response_format,"vision_enabled":BOOL},("model_config_id",)),
    input_schema=obj({"prompt":{"type":"string","maxLength":16000},"context":ANY,"variables":OBJECT,
        "image_attachment_ids":{"type":"array","minItems":1,"maxItems":4,"uniqueItems":True,"items":{"type":"string","pattern":"^[A-Za-z0-9_-]{1,100}$"}}}),
    output_schema=obj({"text":STRING,"mode":enum("model"),"usage":OBJECT,"structured_output":OBJECT},("text","mode"),additional=True))
DEFINITIONS["agent"].update(config_schema=obj({"agent_id":STRING,"agent_version_id":STRING},("agent_id",)),input_schema=obj({"prompt":STRING,"query":OBJECT,"inputs":OBJECT},("prompt",)),output_schema=obj({"text":STRING,"mode":enum("demo","model"),"plan":OBJECT,"tool_executions":ARRAY,"structured_output":ANY,"facts":ARRAY,"source_refs":ARRAY},("text","mode"),additional=True))
DEFINITIONS["parameter_extractor"].update(config_schema=obj({"mode":enum("demo","model"),"model_config_id":STRING,"schema":OBJECT},("mode","schema")),input_schema=obj({"text":STRING},("text",)),output_schema=obj({"parameters":OBJECT,"mode":enum("demo","model")},("parameters","mode")))
DEFINITIONS["question_classifier"].update(config_schema=obj({"mode":enum("demo","model"),"model_config_id":STRING,"classes":{"type":"array","minItems":1,"maxItems":16,"items":obj({"id":STRING,"label":STRING,"keywords":{"type":"array","items":STRING}},("id","label","keywords"))}},("mode","classes")),input_schema=obj({"question":STRING},("question",)),output_schema=obj({"class_id":STRING,"mode":enum("demo","model")},("class_id","mode")))
for model_node in ("parameter_extractor", "question_classifier"):
    DEFINITIONS[model_node]["config_schema"]["properties"]["model_config_version"] = STRING
DEFINITIONS["document_extractor"].update(config_schema=obj({"chunk_size":integer(200,2000),"chunk_overlap":integer(0,200)}),input_schema=obj({"file_id":STRING},("file_id",)),output_schema=obj({"file_id":STRING,"chunks":ARRAY,"chunk_count":{"type":"integer"},"text_length":{"type":"integer"},"source_refs":ARRAY,"extraction_method":STRING,"warnings":ARRAY},("file_id","chunks"),additional=True))
DEFINITIONS["knowledge_retrieval"].update(config_schema=obj({"top_k":integer(1,20)}),input_schema=obj({"query":STRING,"file_ids":{"type":"array","minItems":1,"items":STRING}},("query","file_ids")),output_schema=obj({"method":STRING,"hits":ARRAY,"source_refs":ARRAY,"warnings":ARRAY},("method","hits"),additional=True))
for spec in DEFINITIONS.values():
    if spec["service"]:
        spec["output_schema"].setdefault("properties",{}).update(source_refs=ARRAY,warnings=ARRAY)
        if spec["type"] in ("llm","agent"):spec["input_schema"].setdefault("properties",{})["message"]=OBJECT
DEFINITIONS["knowledge_retrieval"]["input_schema"]=obj({"query":STRING,"file_ids":{"type":"array","minItems":1,"items":STRING},"knowledge_base_ids":{"type":"array","minItems":1,"items":STRING},"version_ids":{"type":"array","minItems":1,"items":STRING}},("query",))
DEFINITIONS["knowledge_retrieval"]["output_schema"]["properties"].update(used_versions=ARRAY,used_chunks=ARRAY,snapshot_id=STRING)
define("code","代码执行","advanced",reason="Unavailable: no isolated sandbox; API-process eval/exec/shell is prohibited")
define("human_approval","人工审批","advanced",obj({"title":{"type":"string","minLength":1,"maxLength":200},"approver_ids":{"type":"array","minItems":1,"maxItems":16,"uniqueItems":True,"items":STRING},"expires_seconds":integer(1,86400),"allow_self":BOOL},("title","approver_ids","expires_seconds")),outputs=obj({"approved":BOOL,"approval_id":STRING},("approved","approval_id")))
define("trigger","定时/事件触发","advanced",reason="Unavailable: durable external trigger scheduler is not implemented")
for message_type,label in (("text","文本消息"),("image","图片消息"),("audio","语音消息"),("file","文件消息"),("mixed","混合消息")):
    define("trigger_"+message_type,label,"trigger",config=obj({"message_field":STRING,"attachments_field":STRING}),outputs=obj({"inputs":OBJECT,"query":OBJECT,"prompt":STRING,"message":OBJECT},additional=True))
DEFINITIONS["output"]["config_schema"]=obj({"presentation":enum("auto","text","table","files","mixed","json")})
DEFINITIONS["output"]["output_schema"]["properties"]["presentation"]=OBJECT
reasoning_schema = obj({"available":BOOL,"text":{"type":"string","maxLength":4000},
    "kind":enum("provider_summary","provider_explanation","provider_reasoning"),
    "source":enum("provider.message.reasoning_summary","provider.message.reasoning_explanation","provider.message.reasoning_content","provider.delta.reasoning_content"),
    "status":enum("available","truncated","disabled","not_returned","redacted","not_adapted"),
    "truncated":BOOL,"profile_id":enum("aliyun_public_reasoning_v1","ark_public_reasoning_v1","ark_public_summary_v1","deepseek_public_reasoning_v1"),
    "protocol":enum("openai_chat_completions"),"profile_verification":enum("administrator_declared"),
    "model_revision":{"type":"string","maxLength":200},
    "unavailable_reason":STRING},("available",))
for model_kind in ("llm","agent","parameter_extractor","question_classifier"):
    DEFINITIONS[model_kind]["output_schema"]["properties"].update(reasoning=reasoning_schema,
        finish_reason={"type":"string","maxLength":64},completion_truncated=BOOL)

# Additive field mapping; service protocol schemas remain authoritative.
define('transform','字段转换','transform',obj(),obj(additional=True),obj(additional=True))
for _definition in DEFINITIONS.values():
    _definition['config_schema'].setdefault('properties',{})['input_mapping']=OBJECT
    _definition['config_schema']['properties']['output_mapping']=OBJECT
DEFINITIONS['agent']['config_schema']['properties']['response_format']=response_format
# Editing a code node does not enable its execution capability.
DEFINITIONS['code'].update(configurable=True)
DEFINITIONS['code']['config_schema']=obj({'language':enum('python','javascript'),'code':{'type':'string','maxLength':100000},'source':{'type':'string','maxLength':100000},'input_mapping':OBJECT,'output_mapping':OBJECT})
for _kind in ('llm','agent'):
    DEFINITIONS[_kind]['config_schema']['properties']['prompt_contract']=enum('local_inputs_v1')
DEFINITIONS['llm']['config_schema']['properties']['prompt_input_schema']=OBJECT
DEFINITIONS['agent']['config_schema']['properties']['user_prompt']={'type':'string','maxLength':16000}


def node_registry():
    items = [deepcopy(definition(kind)) for kind in DEFINITIONS]
    for item in items:
        if item["service"]:
            try: available = callable(getattr(importlib.import_module(item["service"]),"execute_node",None))
            except (ImportError,AttributeError,SyntaxError): available = False
            if not available:
                item.update(status="unavailable",reason="Module integration pending: " + item["service"] + ".execute_node")
            elif item["owner"]=="04":
                reason="Explicit document IDs; text extraction/keyword retrieval only. PDF dependency/OCR status: /api/v1/documents/capabilities"
                if item["type"]=="database_query":reason="Controlled table IDs/fields only; caller data grants and row/column policy apply; no arbitrary SQL"
                item.update(status="supported",reason=reason)
    return {"version":"2.1.0","items":items,"limits":{"max_nodes":64,"max_edges":256,"max_steps":512,"max_iterations":50,"max_depth":3,"max_concurrency":4,"max_condition_depth":3,"max_conditions":32,"timeout_seconds":120,"node_output_bytes":1048576,"variable_pool_bytes":4194304}}

def definition(kind):
    result=DEFINITIONS.get(kind)
    if kind=="database_query":
        try:
            module=importlib.import_module("app.database.nodes")
            schema=getattr(module,"NODE_SCHEMAS",{}).get(kind,{})
            return {**result,**{key:deepcopy(schema[key]) for key in ("config_schema","input_schema","output_schema") if key in schema}}
        except ImportError:pass
    return result
