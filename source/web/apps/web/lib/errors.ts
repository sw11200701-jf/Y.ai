export type FormattedError = { message: string; code?: string; request_id?: string };
type ErrorContext = { endpointIndex?: number };
const labels: Record<string,string> = { name:'名称',description:'说明',username:'账号',password:'密码',new_password:'新密码',current_password:'当前密码',base_url:'服务地址',path:'相对路径',tool_id:'工具',connection_id:'服务连接',schema_fields:'数据字段',file:'文件',text:'消息内容',role:'角色',page_permissions:'界面权限' };
const codes:Record<string,string>={AUTH_REQUIRED:'登录已失效，请重新登录。',INVALID_CREDENTIALS:'账号或密码不正确。',FORBIDDEN:'当前账号没有此操作权限。',PERMISSION_DENIED:'当前账号没有此操作权限。',NOT_FOUND:'内容不存在或当前账号无权访问。',CSRF_INVALID:'安全验证已过期，请重试。',MODEL_NOT_CONFIGURED:'尚未配置模型，请先完成本地模型配置。',MODEL_CAPABILITY_UNAVAILABLE:'当前模型不支持所需的图片或语音能力。',NODE_UNAVAILABLE:'这个节点暂不支持执行。',QUALITY_FAILED:'数据质量检查未通过，请先修正问题。',SERVICE_UNAVAILABLE:'服务暂不可用，请稍后重试。',CONFLICT:'内容已变化或存在冲突，请刷新确认后重试。',FIELD_DEFINITION_INVALID:'字段配置不符合要求，请检查类型、默认值与位置。',PARAMETER_POSITION_INVALID:'参数位置配置有误，请检查路径、查询或正文设置。',OUTPUT_MAPPING_INVALID:'响应字段映射不匹配，请检查响应路径与类型。',TOOL_ERROR:'接口调用未完成，请检查服务配置和返回内容。'};
Object.assign(codes,{
  INTERNAL_ERROR:'服务遇到问题，请稍后重试。',
  REQUEST_BUSY:'服务正忙，请稍后重试。',
  REQUEST_OUTCOME_UNCONFIRMED:'提交结果待确认，请先查看运行记录。',
  OUTCOME_UNKNOWN:'执行结果待确认，请先查看运行记录。',
});
export function formatError(error:unknown,context:ErrorContext={}):FormattedError {
  if(!error)return {message:''};
  if(typeof error==='string')return {message:/^[\x00-\x7f]*$/.test(error)&&error?'操作未完成，请检查输入后重试。':error};
  const e=(error||{}) as any;if(e.errorFields)return {message:''};
  const details=Array.isArray(e.details)?e.details:Array.isArray(e.error?.details)?e.error.details:[];
  const value=e.code||e.error?.code;
  const code=typeof value==='string'&&/^[A-Z][A-Z0-9_]{0,79}$/.test(value)?value:undefined;
  const request_id=typeof e.request_id==='string'&&/^[a-f0-9]{32}$/i.test(e.request_id)?e.request_id:undefined;
  if(e.message===''&&!code&&!details.length)return {message:''};
  const messages=details.map((d:any)=>{
    const loc=Array.isArray(d.loc)?d.loc.filter((x:any)=>x!=='body'):String(d.field||'').split('.');const field=loc.at(-1);const type=d.type||d.code||'';
    if(loc.includes('tools')&&field==='description'){const index=typeof context.endpointIndex==='number'?context.endpointIndex:Number(loc[loc.indexOf('tools')+1])||0;return `第${index+1}个接口的工具说明不能为空`;}
    if(field==='password'&&d.context?.min_length===1)return '请输入密码';
    if(['password','new_password'].includes(field))return /long/.test(type)?'密码最多允许128位':'密码至少需要8位';
    if(field==='username'&&/short/.test(type))return '账号至少需要3位';
    const label=labels[field]||'填写内容';if(/missing|short/.test(type))return `${label}不能为空或长度不足`;
    if(d.message&&/[\u3400-\u9fff]/.test(d.message))return `${label}：${d.message}`;
    return `${label}不符合要求，请检查后重试`;
  });
  const raw=e.message||e.error?.message||'';
  const known=code?codes[code]:undefined;
  let message=e.status>=500?(known||'服务暂时无法完成此操作，请稍后重试。'):messages.length?[...new Set(messages)].join('；'):known||(/[\u3400-\u9fff]/.test(raw)?raw:'操作未完成，请检查输入后重试。');
  if(e.name==='AbortError')message='操作已取消或登录身份已变化。';
  if(e instanceof TypeError||/fetch failed|failed to fetch|networkerror|econnrefused/i.test(raw))message='暂时无法连接服务，请检查连接后重试。';
  if(e.name==='TimeoutError')message='服务响应超时，请稍后重试。';
  return {message,...(code?{code}:{}),...(request_id?{request_id}:{})};
}
export function errorMessage(error:unknown,context?:ErrorContext){return formatError(error,context).message;}
