"""Bounded literal-variable prompts and independent structured-output validation."""

import json
import re
from jsonschema import Draft202012Validator
from referencing import Registry
from app.connections.security import redact
from app.errors import AppError

PATH = re.compile(r"^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*(?:\.(?:[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*|[0-9]+))*$")
TOKEN = re.compile(r"{{(.*?)}}", re.S)


def validate_local_templates(config, names, *, allow_system_inputs=True):
    """Validate declarations before substitutions; input strings are never re-templated."""
    messages = config.get("prompt_messages")
    if messages is None:
        messages = [{"role": "system", "content": config.get("system_prompt", "")},
                    {"role": "user", "content": config.get("user_prompt", "")}]
    for index, message in enumerate(messages):
        text = message.get("content")
        loc = ("config", "prompt_messages", index, "content")
        if not isinstance(text, str):
            field_error("提示消息必须为文本", loc)
        remainder = TOKEN.sub("", text)
        if any(marker in remainder for marker in ("{{", "}}", "{%")):
            field_error("变量标记不完整或包含不支持的表达式", loc)
        for token in TOKEN.finditer(text):
            path = token.group(1).strip()
            if not PATH.fullmatch(path) or path.split(".")[0] not in names:
                field_error("提示词只能引用本节点已声明输入，变量不存在或已删除", loc, "VARIABLE_NOT_FOUND")
            if message.get("role") == "system" and not allow_system_inputs:
                field_error("系统指令不能插入业务输入，请将变量移至用户消息", loc, "UNTRUSTED_SYSTEM_INPUT")


def field_error(message, loc, code="VALIDATION_ERROR", kind="value_error"):
    raise AppError(code, message, 422, [{"loc": list(loc), "type": kind, "message": message}])


def render(template, variables, loc, *, max_rendered_chars=16000):
    if type(max_rendered_chars) is not int or not 16000<=max_rendered_chars<=4194304:
        field_error('受信提示词展开预算无效',loc)
    if not isinstance(template, str) or not 1 <= len(template) <= 16000:
        field_error("提示词须为1至16000字符的文本", loc)
    def replace(match):
        path = match.group(1).strip()
        if not PATH.fullmatch(path):
            field_error("变量模板仅支持显式变量名及点路径，不支持表达式", loc)
        value = variables
        for part in path.split("."):
            if type(value) is dict and part in value:
                value = value[part]
            elif type(value) is list and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                field_error("提示词引用的变量不存在", loc, "VARIABLE_NOT_FOUND")
        if value is None:
            field_error("提示词变量为空，不能作为文本使用", loc, "VARIABLE_TYPE_MISMATCH")
        if isinstance(value, str):
            return value
        def json_only(item):
            if type(item) is dict:
                return all(type(key) is str and json_only(child) for key, child in item.items())
            if type(item) is list:
                return all(json_only(child) for child in item)
            return item is None or type(item) in (str, int, float, bool)
        try:
            if not json_only(value):
                raise ValueError()
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (ValueError, TypeError, RecursionError):
            field_error("提示词变量类型不支持或不是有限JSON值", loc, "VARIABLE_TYPE_MISMATCH")
    #Ordinary single braces and JSON are literal. Only double braces are templates.
    remainder = TOKEN.sub("", template)
    if "{{" in remainder:
        field_error("变量模板缺少结束标记", loc)
    rendered = TOKEN.sub(replace, template)
    if len(rendered) > max_rendered_chars:
        field_error(f"变量替换后的提示词超过{max_rendered_chars}字符", loc)
    return rendered


def normalize_response_format(value):
    if value is None:
        return None
    loc = ("config", "response_format")
    if type(value) is not dict or set(value) - {"type", "schema", "name", "strict"} or value.get("type") not in {"text", "json_object", "json_schema"}:
        field_error("输出格式配置无效", loc)
    result = dict(value)
    if "strict" in result and type(result["strict"]) is not bool:
        field_error("严格结构开关须为布尔值", (*loc, "strict"))
    if "name" in result and (not isinstance(result["name"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", result["name"])):
        field_error("结构名称仅支持1至64位字母、数字、下划线或短横线", (*loc, "name"))
    schema = result.get("schema")
    if result["type"] == "text" and len(result) != 1:
        field_error("文本输出不接受结构Schema配置", loc)
    if result["type"] == "json_object" and result.get("strict"):
        field_error("JSON模式仅做JSON与本地结构校验，不支持原生严格Schema开关", (*loc, "strict"))
    if result["type"] == "json_schema" and type(schema) is not dict:
        field_error("结构化输出须填写JSON Schema", (*loc, "schema"))
    if schema is not None:
        try:
            if type(schema) is not dict or len(json.dumps(schema, allow_nan=False).encode()) > 32768:
                raise ValueError()
            def walk(item, depth=0):
                if depth > 24:
                    raise ValueError()
                if isinstance(item, dict):
                    if "$dynamicRef" in item or "$recursiveRef" in item:
                        raise ValueError()
                    if "$ref" in item and (not isinstance(item["$ref"], str) or not item["$ref"].startswith("#/$defs/")):
                        raise ValueError()
                    for child in item.values():
                        walk(child, depth + 1)
                elif isinstance(item, list):
                    for child in item:
                        walk(child, depth + 1)
            walk(schema)
            Draft202012Validator.check_schema(schema)
        except Exception:
            field_error("输出Schema无效或含外部引用，仅支持本地$defs引用", (*loc, "schema"))
    return result


def render_messages(config, inputs):
    variables = inputs.get("variables", {})
    if type(variables) is not dict:
        field_error("提示词变量须为显式对象", ("inputs", "variables"))
    if config.get("prompt_contract") == "local_inputs_v1":
        schema = config.get("prompt_input_schema")
        if schema is not None:
            from app.runner.input_defaults import normalize_inputs
            variables = normalize_inputs(variables, schema)
            try:
                Draft202012Validator(schema, registry=Registry()).validate(variables)
            except Exception as exc:
                field_error("本节点提示词输入不符合声明的类型或必填约束", ("inputs", "variables"), "VARIABLE_TYPE_MISMATCH")
        validate_local_templates(config, variables)
    if "prompt_messages" in config:
        prompts = config["prompt_messages"]
        if "system_prompt" in config or inputs.get("prompt"):
            field_error("消息提示词不能与旧System或非空prompt同时使用", ("config", "prompt_messages"))
        if not isinstance(prompts, list) or not 1 <= len(prompts) <= 16:
            field_error("请配置1至16条角色消息", ("config", "prompt_messages"))
        messages = []
        for index, item in enumerate(prompts):
            loc = ("config", "prompt_messages", index)
            if type(item) is not dict or set(item) != {"role", "content"} or item.get("role") not in {"system", "user", "assistant"}:
                field_error("消息角色或字段无效", loc)
            if item["role"] == "system" and index != 0:
                field_error("System消息仅可放在第一条", (*loc, "role"))
            messages.append({"role": item["role"], "content": render(item["content"], variables, (*loc, "content"))})
        if not any(item["role"] == "user" for item in messages):
            field_error("至少需要一条User消息", ("config", "prompt_messages"))
    else:
        prompt = inputs.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            field_error("LLM节点需要非空prompt（至多16000字符）", ("inputs", "prompt"))
        system = config.get("system_prompt", "")
        if not isinstance(system, str) or len(system) > 16000:
            field_error("System提示词须为至多16000字符文本", ("config", "system_prompt"))
        if "variables" in inputs:
            system = render(system, variables, ("config", "system_prompt")) if system else system
            prompt = render(prompt, variables, ("inputs", "prompt"))
        messages = [{"role": "system", "content": system + "\nReturn the answer only; do not include hidden reasoning."}, {"role": "user", "content": prompt}]
    if inputs.get("context") is not None:
        try:
            context = json.dumps(redact(inputs["context"]), ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            field_error("上下文须为有限JSON值", ("inputs", "context"), "VARIABLE_TYPE_MISMATCH")
        index = max(i for i, message in enumerate(messages) if message["role"] == "user")
        messages[index]["content"] += "\nContext data: " + context
    output = normalize_response_format(config.get("response_format"))
    if output and output["type"] in {"json_object", "json_schema"}:
        instruction = "Return valid JSON only."
        if messages[0]["role"] == "system":
            messages[0]["content"] += "\n" + instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    return messages, output


def validate_output(text, response_format):
    if not response_format or response_format["type"] == "text":
        return None
    try:
        parsed = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if response_format["type"] == "json_object" and type(parsed) is not dict:
            raise ValueError()
        if response_format.get("schema") is not None:
            # No remote retrieval, including references rebased by a nested $id.
            Draft202012Validator(response_format["schema"], registry=Registry()).validate(parsed)
        return parsed
    except Exception as exc:
        raise AppError("OUTPUT_SCHEMA_MISMATCH", "模型返回内容不符合JSON或配置的输出结构", 422) from exc
