"""Bounded text-only cURL import. Preview never executes, reads files or persists.

Confirmation re-parses text and delegates to existing connection/plugin services.
Credentials never become endpoint defaults, public DTOs, logs or plugin versions.
"""
from dataclasses import dataclass,field
import hashlib
import json
import re
import shlex
from urllib.parse import parse_qsl,urlsplit,unquote
from pydantic import Field,SecretStr,StrictBool,model_validator
from app.errors import AppError
from app.db import get_resource,Resource
from sqlalchemy import select
from app.connections.security import origin,validate_base_url
from .fields import FieldDefinition,NAME,PROTECTED_HEADERS
from .specs import StrictModel,EndpointDraft,PluginDraft,ToolDraft,RESERVED_ARGUMENTS


class CurlConfirmation(StrictModel):
    expected_preview_fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')
    connection_id:str|None=None
    create_connection_name:str|None=Field(default=None,min_length=1,max_length=100)
    plugin_id:str|None=None
    expected_plugin_revision:int|None=Field(default=None,ge=1)
    plugin_name:str|None=Field(default=None,min_length=1,max_length=100)
    endpoint_id:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    endpoint_name:str=Field(min_length=1,max_length=100)
    tool_id:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    tool_name:str=Field(min_length=1,max_length=100)
    tool_description:str=Field(min_length=1,max_length=2000)
    output_fields:list[FieldDefinition]=Field(max_length=128)
    import_credentials:StrictBool=False
    @model_validator(mode='after')
    def selection(self):
        if bool(self.connection_id)==bool(self.create_connection_name):raise ValueError('请选择现有服务或填写新服务名称')
        if bool(self.plugin_id)==bool(self.plugin_name):raise ValueError('请选择现有插件或填写新插件名称')
        if bool(self.plugin_id)!=(self.expected_plugin_revision is not None):raise ValueError('追加插件需要当前草稿版本')
        return self


@dataclass(repr=False)
class _Parsed:
    public:dict
    credential:SecretStr|None=field(default=None,repr=False)


def _error(message):raise AppError('CURL_IMPORT_INVALID',message,422)


def _tokens(text):
    if not isinstance(text,str) or not 0<len(text.encode('utf-8'))<=32768:_error('cURL文本须为1至32768字节')
    quote=None;escaped=False;out=[];i=0
    while i<len(text):
        c=text[i]
        if c=='\\' and quote!="'" and i+1<len(text) and text[i+1] in '\r\n':
            i+=2
            if text[i-1]=='\r' and i<len(text) and text[i]=='\n':i+=1
            continue
        if escaped:out.append(c);escaped=False;i+=1;continue
        if c=='\\' and quote!="'":out.append(c);escaped=True;i+=1;continue
        if c in "'\"":
            if quote==c:quote=None
            elif quote is None:quote=c
        elif quote is None and (c in ';&|<>`' or text[i:i+2] in ('$(',"$'")):_error('不支持引号外的shell操作符或命令替换；不会执行命令')
        if ord(c)<32 and c not in '\r\n\t':_error('cURL文本含不支持的控制字符')
        out.append(c);i+=1
    if quote or escaped:_error('cURL引号或转义未闭合')
    try:tokens=shlex.split(''.join(out),posix=True)
    except ValueError:_error('cURL引号格式无效')
    if not tokens or tokens[0] not in {'curl','curl.exe'}:_error('请输入单条curl命令文本')
    if len(tokens)>512:_error('cURL参数超过512项')
    return tokens[1:]


def _sensitive(name):
    value=re.sub(r'[^a-z0-9]','',str(name).lower())
    return value in {re.sub(r'[^a-z0-9]','',s) for s in RESERVED_ARGUMENTS}|{'cookie','setcookie','apikey','accesskey','accesskeyid','accesskeysecret','passwd','key','xkey','auth','session','sessionid','sig','sign'} or any(s in value for s in ('password','secret','credential','token','signature','apikey','cookie','authorization','sessionid'))


def _field(name,value,position,depth=1):
    if depth>6 or not NAME.fullmatch(name):_error('字段名或嵌套层数暂不支持，请手工配置该接口')
    if _sensitive(name):_error('查询或正文包含凭据或受保护字段，不能作为业务字段导入')
    kind={str:'string',bool:'boolean',int:'integer',float:'number',dict:'object',list:'array'}.get(type(value))
    if kind is None:_error('null或未知字段无法可靠推断类型，请手工配置字段')
    result={'name':name,'type':kind,'required':True}
    if depth==1:result['position']=position
    if kind=='object':result['children']=[_field(k,v,position,depth+1) for k,v in value.items()]
    elif kind=='array':
        if not value:_error('空数组无法可靠推断元素类型，请手工配置字段')
        item=_field('item',value[0],position,depth+1)
        def shape(v):return {k:shape(x) for k,x in v.items() if k not in {'name','default','position'}} if isinstance(v,dict) else [shape(x) for x in v] if isinstance(v,list) else v
        if any(shape(_field('item',v,position,depth+1))!=shape(item) for v in value[1:]):_error('混合类型数组暂不支持自动导入')
        result['item']=item
    result['default']=value
    return result


def _pairs(value):
    try:pairs=parse_qsl(value,keep_blank_values=True,max_num_fields=128,errors='strict')
    except ValueError:_error('查询或表单字段超过128项或包含非UTF-8编码')
    result={}
    for key,val in pairs:
        if _sensitive(key):_error('查询或正文中的认证参数不能导入为业务默认值')
        if key in result:result[key]=result[key]+[val] if isinstance(result[key],list) else [result[key],val]
        else:result[key]=val
    return result


def _parse(command):
    tokens=_tokens(command);url=None;method=None;headers=[];data=[];forms=[];get=False;i=0
    values={'-X':'method','--request':'method','-H':'header','--header':'header','-d':'data','--data':'data','--data-raw':'raw','--data-urlencode':'urlencoded','--url':'url','-F':'form','--form':'form','--form-string':'form_string'}
    while i<len(tokens):
        token=tokens[i];i+=1
        if token in {'-G','--get'}:get=True;continue
        option,sep,value=token.partition('=') if token.startswith('--') else (token,'','')
        if option not in values and token[:2] in {'-X','-H','-d','-F'} and len(token)>2:option,value,sep=token[:2],token[2:],'='
        if option in values:
            if not sep:
                if i>=len(tokens):_error('cURL选项缺少值')
                value=tokens[i];i+=1
            kind=values[option]
            if kind=='method':
                if method is not None:_error('不支持重复请求方法')
                method=value.upper()
            elif kind=='header':headers.append(value)
            elif kind in {'data','raw','urlencoded'}:
                if value.startswith('@') or (kind=='urlencoded' and ('@' in value.split('=',1)[0])):_error('不支持@本地文件，不会读取文件')
                data.append((kind,value))
            elif kind in {'form','form_string'}:
                if '=' not in value or (kind=='form' and (value.split('=',1)[1].startswith(('@','<')) or ';' in value)):_error('表单仅支持文本字段，不支持文件、元数据或读取重定向')
                forms.append(value)
            else:
                if url is not None:_error('只支持单个请求URL')
                url=value
        elif token.startswith('-'):_error('包含暂不支持的cURL选项；不会忽略或执行它')
        elif url is None:url=token
        else:_error('不支持多个URL或额外命令')
    if not url:_error('cURL缺少HTTP服务地址')
    if any(c.isspace() or ord(c)<32 for c in url):_error('服务地址不能含未编码的空白或控制字符')
    try:
        parsed=urlsplit(url)
        if parsed.username or parsed.password or parsed.fragment:_error('URL不能携带用户名、密码或片段')
        base=origin(url);base=validate_base_url(base)
    except (ValueError,TypeError):_error('服务地址不是有效的无凭据HTTP(S)地址')
    except AppError:raise
    except Exception:_error('服务地址不符合现有连接规则')
    path=parsed.path or '/'
    auth='none';credential=None;key_header='X-API-Key';fields=[];ctype=None
    for header in headers:
        if ':' not in header:_error('请求头格式应为名称:值')
        name,value=(s.strip() for s in header.split(':',1));lower=name.lower()
        if any(ord(c)<32 or ord(c)>126 for c in name+value):_error('请求头只支持无换行ASCII文本')
        if 'cookie' in lower:_error('Cookie会话认证暂不支持自动导入，请使用受控连接认证')
        if lower=='content-type':
            if ctype is not None:_error('不支持重复Content-Type')
            parameters=value.split(';')
            ctype=parameters[0].strip().lower()
            if ctype!='multipart/form-data' and any(p.strip().lower().replace(' ','') not in {'charset=utf-8','charset="utf-8"'} for p in parameters[1:]):_error('仅支持UTF-8正文编码，请移除不支持的Content-Type参数')
            continue
        if lower=='accept':
            if value not in {'application/json','*/*'}:_error('当前接口仅支持JSON响应，请调整Accept')
            continue
        if lower=='authorization' or _sensitive(name):
            if credential is not None:_error('只支持一个受控认证凭据')
            if lower=='authorization' and value.lower().startswith('bearer '):auth='bearer';raw=value[7:]
            elif lower.startswith('x-') and re.fullmatch(r'[Xx]-[A-Za-z0-9-]{1,60}',name) and 'signature' not in lower:auth='api_key';key_header=name;raw=value
            else:_error('该认证头暂不能安全导入；请使用现有Bearer或X-API-Key连接')
            if not raw or len(raw)>4096 or any(c in raw for c in '\r\n'):_error('认证值格式无效')
            credential=SecretStr(raw)
        else:
            if lower in PROTECTED_HEADERS or not lower.startswith('x-'):_error('仅支持业务X-请求头，受保护或浏览器专用头请移除')
            fields.append(_field(name,value,'header'))
    if forms and data:_error('不支持混合JSON/数据参数与表单')
    method=method or ('GET' if get or not (data or forms) else 'POST')
    if method not in {'GET','POST'}:_error('现有只读插件仅支持GET/POST，不能自动导入该方法')
    if get and method!='GET':_error('-G与显式方法冲突')
    query=_pairs(parsed.query);body={};encoding='json'
    if data:
        if ctype=='application/json':
            if len(data)!=1 or get:_error('JSON正文需要单个数据参数，不能与-G混用')
            try:body=json.loads(data[0][1],parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            except (ValueError,TypeError,RecursionError):_error('JSON请求体无效')
            if not isinstance(body,dict):_error('JSON正文只支持对象，请手工配置其他形状')
        elif ctype in {None,'application/x-www-form-urlencoded'}:
            parts=[]
            from urllib.parse import quote
            for kind,value in data:
                if kind=='urlencoded':
                    if '=' not in value:_error('--data-urlencode仅支持name=value')
                    key,val=value.split('=',1);value=quote(key,safe='')+'='+quote(val,safe='')
                if '=' not in value:_error('非JSON数据仅支持name=value；JSON请声明Content-Type')
                parts.append(value)
            body=_pairs('&'.join(parts));encoding='form_urlencoded'
        else:_error('该Content-Type暂不支持自动导入')
    if forms:
        if ctype not in {None,'multipart/form-data'}:_error('表单与Content-Type不一致')
        from urllib.parse import urlencode
        body=_pairs(urlencode([tuple(v.split('=',1)) for v in forms]));encoding='multipart'
    if get:
        if forms:_error('-G不能与multipart表单混用')
        if set(query)&set(body):_error('URL查询与-G数据字段重复，请明确合并')
        query.update(body);body={};encoding='json'
    elif method=='GET' and body:_error('GET正文不能被现有插件安全表达，请使用-G')
    fields.extend(_field(k,v,'query') for k,v in query.items());fields.extend(_field(k,v,'body') for k,v in body.items())
    if len({f['name'] for f in fields})!=len(fields):_error('不同位置出现同名字段，请手工明确参数映射')
    if credential is not None:
        secret=credential.get_secret_value()
        if secret in path or secret in base or any(secret in json.dumps(f,ensure_ascii=False) for f in fields):_error('认证值同时出现在地址或业务字段中，请先移除重复凭据')
    try:
        endpoint=EndpointDraft(id='imported',name='导入接口',method=method,path=path,request_encoding=encoding,
            input_fields=fields,output_schema={'type':'object'})
    except Exception:_error('字段或路径不能通过现有插件校验，请手工配置')
    public={'schema_version':'1.0','can_confirm':True,'connection':{'base_url':base,'auth_type':auth,'api_key_header':key_header},
        'endpoint':endpoint.model_dump(mode='json',exclude={'id','name','operation_id'}),'credential_requirements':[] if credential is None else [{'location':'header','name':'Authorization' if auth=='bearer' else key_header,'auth_type':auth,'masked_value':'[REDACTED]'}],
        'response_schema_required':True,'diagnostics':['仅解析请求，未执行命令、读取本地文件或发送请求；响应字段需手工填写或从明确样例推断。']}
    canonical=json.dumps(public,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    if len(canonical)>262144:_error('预览结构过大，请精简请求样例后导入')
    public['fingerprint']=hashlib.sha256(canonical).hexdigest()
    return _Parsed(public,credential)


def preview_curl(command:str)->dict:return _parse(command).public


async def confirm_curl(command:str,selection:CurlConfirmation|dict,*,context,session)->dict:
    from app.connections.service import ConnectionWrite,save_connection,public_connection
    from .permissions import require_page
    from .service import create_plugin,update_plugin
    require_page(context,session,'page.plugins')
    try:choice=selection if isinstance(selection,CurlConfirmation) else CurlConfirmation.model_validate(selection)
    except Exception:_error('导入确认字段无效，请核对服务、插件、版本和输出字段')
    parsed=_parse(command);preview=parsed.public
    if choice.expected_preview_fingerprint!=preview['fingerprint']:raise AppError('CURL_PREVIEW_CHANGED','预览已变化，请重新预览后确认',409)
    if parsed.credential is not None and parsed.credential.get_secret_value() in choice.model_dump_json():_error('接口名称或描述不能包含认证值')
    connection=None;new_connection=None;path=preview['endpoint']['path']
    if choice.connection_id:
        if choice.import_credentials:_error('复用服务不会覆盖其认证，请在服务设置中更新')
        value=get_resource(session,'connection',choice.connection_id,context)
        conn_url=urlsplit(value['base_url']);prefix=conn_url.path.rstrip('/')
        if origin(value['base_url'])!=preview['connection']['base_url'] or (prefix and not path.startswith(prefix+'/')):_error('该接口URL不属于所选服务的域名及基址路径')
        if prefix:path=path[len(prefix):]
        if not value.get('enabled',True):_error('所选服务已停用')
        if preview['connection']['auth_type']!='none' and (value.get('auth_type')!=preview['connection']['auth_type'] or (value.get('auth_type')=='api_key' and value.get('api_key_header','X-API-Key').lower()!=preview['connection']['api_key_header'].lower())):_error('cURL认证方式与所选服务不同，请明确配置服务认证')
        if preview['connection']['auth_type']!='none' and not value.get('secret_ref'):_error('所选服务尚未配置认证，请先在服务设置中配置')
        connection=public_connection(value)
    else:
        if parsed.credential is not None and not choice.import_credentials:_error('请求含认证，创建服务前请明确选择安全保存认证')
        new_connection=ConnectionWrite(name=choice.create_connection_name,**preview['connection'],secret=parsed.credential if choice.import_credentials else None)
    try:endpoint=EndpointDraft(**{**{k:v for k,v in preview['endpoint'].items() if k not in {'output_schema','output_fields'}},'path':path,'id':choice.endpoint_id,'operation_id':choice.endpoint_id,'name':choice.endpoint_name,'output_fields':[f.model_dump(mode='json') for f in choice.output_fields]})
    except Exception:_error('接口名称或响应字段无效，请手工核对输出配置')
    previous=None
    if choice.plugin_id:
        previous=get_resource(session,'plugin',choice.plugin_id,context,access='write')
        if previous.get('status')=='archived':raise AppError('CONFLICT','已归档插件不能修改',409)
        if previous.get('revision')!=choice.expected_plugin_revision:raise AppError('CONFLICT','插件草稿已变化，请重新确认',409)
        if new_connection and previous.get('default_connection_id'):_error('追加接口请复用现有插件的默认服务，不能隐式改绑')
        if connection and previous.get('default_connection_id') not in {None,connection['id']}:_error('现有插件默认服务不同，不能隐式改绑已有接口')
    endpoints=(previous or {}).get('endpoints',[])+[endpoint.model_dump(mode='json')]
    tools=(previous or {}).get('tools',[])+[ToolDraft(id=choice.tool_id,name=choice.tool_name,description=choice.tool_description,endpoint_id=choice.endpoint_id).model_dump()]
    try:draft=PluginDraft(name=previous['name'] if previous else choice.plugin_name,description=(previous or {}).get('description',''),endpoints=endpoints,tools=tools,default_connection_id=connection['id'] if connection else None)
    except Exception:_error('接口或工具ID重复，或响应字段不符合插件要求')
    with session.begin_nested():
        if new_connection:
            connection=await save_connection(new_connection,context,session)
            draft=draft.model_copy(update={'default_connection_id':connection['id']})
        if previous:
            session.scalar(select(Resource).where(Resource.kind=='plugin',Resource.resource_id==choice.plugin_id,
                Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id).with_for_update().execution_options(populate_existing=True))
            latest=get_resource(session,'plugin',choice.plugin_id,context,access='write')
            if latest.get('revision')!=choice.expected_plugin_revision:raise AppError('CONFLICT','插件草稿已变化，请重新确认',409)
        plugin=update_plugin(choice.plugin_id,draft,context,session) if previous else create_plugin(draft,context,session)
    return {'status':'draft','plugin':plugin,'connection':connection,'endpoint_id':endpoint.id}
