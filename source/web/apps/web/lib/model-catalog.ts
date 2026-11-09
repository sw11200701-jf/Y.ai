import { request } from './api';
export const parameterLabels:Record<string,string>={temperature:'随机性',top_p:'采样范围',max_tokens:'最大输出长度',frequency_penalty:'重复惩罚',presence_penalty:'话题惩罚',stop:'停止词',seed:'随机种子'};
export type ReasoningProfile={profile_id:'aliyun_public_reasoning_v1'|'ark_public_reasoning_v1'|'ark_public_summary_v1'|'deepseek_public_reasoning_v1';protocol:'openai_chat_completions';model_id:string;model_revision:string;transport:'json'|'sse';request_control:'none'|'enable_thinking'|'thinking_type';mode:'provider_default'|'enabled'|'disabled'|'auto'};
export type VisionProfile={profile_id:'openai_image_url_v1';protocol:'openai_chat_completions';model_id:string;model_revision:string;input_source:'current_run_message_attachment';token_reservation:'tiles512_2048_plus1024_v1'};
export const reasoningProfileNames={aliyun_public_reasoning_v1:'阿里云 Chat · 供应商公开推理内容',ark_public_reasoning_v1:'方舟 Chat · 供应商公开推理内容',ark_public_summary_v1:'方舟 Chat · 供应商公开摘要',deepseek_public_reasoning_v1:'DeepSeek Chat · 供应商公开推理内容'};
export type ModelChoice={id:string;name:string;provider_name:string;source:string;config_version:string;context_window:number;max_output_tokens:number;capabilities:{parameters:string[];tools:boolean;json_object:boolean;json_schema:boolean;reasoning_summary?:boolean;reasoning_explanation?:boolean;reasoning_profile?:ReasoningProfile;vision_profile?:VisionProfile}};
export function catalogItems(body:unknown):ModelChoice[]{
  const items=(body as {items?:unknown})?.items;
  if(!Array.isArray(items))throw Error('模型目录格式异常，请重试。');
  return items.map(m=>{if(!m||!['id','name','provider_name','config_version','source'].every(k=>typeof m[k]==='string')||!Number.isInteger(m.context_window)||!Number.isInteger(m.max_output_tokens)||!Array.isArray(m.capabilities?.parameters)||m.capabilities.parameters.some((p:unknown)=>typeof p!=='string'))throw Error('模型目录资料不完整，请重试。');
    const profile=m.capabilities.reasoning_profile;if(profile!=null&&reasoningProfileIssues(profile).length)throw Error('模型公开推理适配资料不完整，原引用保留，请联系模型管理员。');
    const vision=m.capabilities.vision_profile;if(vision!=null&&visionProfileIssues(vision,typeof m.model_name==='string'?m.model_name:undefined,profile).length)throw Error('模型图片输入适配资料不完整或与公开推理声明不兼容，原引用保留，请联系模型管理员。');
    return {id:m.id,name:m.name,provider_name:m.provider_name,source:m.source,config_version:m.config_version,context_window:m.context_window,max_output_tokens:m.max_output_tokens,capabilities:{parameters:[...m.capabilities.parameters],tools:m.capabilities.tools===true,json_object:m.capabilities.json_object===true,json_schema:m.capabilities.json_schema===true,reasoning_summary:m.capabilities.reasoning_summary===true,reasoning_explanation:m.capabilities.reasoning_explanation===true,...(profile?{reasoning_profile:{profile_id:profile.profile_id,protocol:profile.protocol,model_id:profile.model_id,model_revision:profile.model_revision,transport:profile.transport,request_control:profile.request_control,mode:profile.mode} as ReasoningProfile}:{}),...(vision?{vision_profile:{profile_id:vision.profile_id,protocol:vision.protocol,model_id:vision.model_id,model_revision:vision.model_revision,input_source:vision.input_source,token_reservation:vision.token_reservation} as VisionProfile}:{})}};
  });
}
export async function modelCatalog(){return catalogItems(await request('/model-catalog'));}
export function modelIssues(model:ModelChoice|undefined,config:Record<string,any>):string[]{
  if(!model)return [];
  const issues:string[]=[];const parameters=config.model_parameters||{};
  if(typeof parameters!=='object'||Array.isArray(parameters))return ['已保存模型参数格式异常，请在高级兼容中处理，原值未删除。'];
  for(const [key,value] of Object.entries(parameters)){
    // Form registration adds undefined optional fields; JSON omits them on save.
    if(value===undefined)continue;
    if(!modelParameterAllowed(model,key)){issues.push(`model_parameters.${key}（${parameterLabels[key]||key}）不受当前模型或公开推理适配支持，请明确移除或选择其他模型。`);continue;}
    const range=parameterRange(key,model);if(range&&(!Number.isFinite(value)||Number(value)<range.min||Number(value)>range.max||(range.step===1&&!Number.isInteger(value))))issues.push(`${parameterLabels[key]}需在${range.min}至${range.max}之间。`);
    if(key==='stop'&&(!Array.isArray(value)||value.length>4||value.some(v=>typeof v!=='string'||!v.length||v.length>200)))issues.push('停止词最多4项，每项1至200字。');
  }
  if(!Number.isInteger(config.context_budget??8192)||(config.context_budget??8192)<512||(config.context_budget??8192)>model.context_window)issues.push(`上下文预算需在512至${model.context_window}之间。`);
  const format=config.response_format?.type;if(format==='json_object'&&!model.capabilities.json_object||format==='json_schema'&&!model.capabilities.json_schema)issues.push('当前模型不支持已保存的结构化输出方式。');
  if(model.capabilities.reasoning_profile&&Array.isArray(config.bindings)&&config.bindings.length)issues.push('bindings（工具绑定）：此公开推理适配尚不支持工具多轮，请明确移除工具绑定或选择其他模型。');
  return issues;
}
export function parameterRange(key:string,model:ModelChoice){return ({temperature:{min:0,max:2,step:0.1},top_p:{min:0,max:1,step:0.05},max_tokens:{min:1,max:Math.min(393216,model.max_output_tokens),step:1},frequency_penalty:{min:-2,max:2,step:0.1},presence_penalty:{min:-2,max:2,step:0.1},seed:{min:-2147483648,max:2147483647,step:1}} as Record<string,{min:number;max:number;step:number}>)[key];}
export function reasoningProfileIssues(value:unknown,modelId?:string):string[]{
 if(value==null)return [];const p=value as Record<string,any>,fields=['profile_id','protocol','model_id','model_revision','transport','request_control','mode'];
 if(typeof p!=='object'||Array.isArray(p)||Object.keys(p).some(k=>!fields.includes(k))||!Object.hasOwn(reasoningProfileNames,p.profile_id))return ['capabilities.reasoning_profile：已有声明暂不能识别，原值保留，请明确移除或重新选择。'];
 const issues:string[]=[];
 if(p.protocol!=='openai_chat_completions')issues.push('capabilities.reasoning_profile.protocol：当前仅支持Chat Completions兼容协议。');
 if(typeof p.model_id!=='string'||!p.model_id||p.model_id.length>200||modelId!==undefined&&p.model_id!==modelId)issues.push('capabilities.reasoning_profile.model_id：声明须与“服务实际model名称”精确一致，请明确重新绑定。');
 if(typeof p.model_revision!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$/.test(p.model_revision)||['latest','auto','default','unknown'].includes(p.model_revision.toLowerCase()))issues.push('capabilities.reasoning_profile.model_revision：请填写具体模型版本，不能使用latest/auto/default/unknown漂移标签。');
 if(!['json','sse'].includes(p.transport))issues.push('capabilities.reasoning_profile.transport：请选择JSON或SSE响应。');
 const control=p.profile_id.startsWith('aliyun_')?['none','enable_thinking']:['none','thinking_type'];
 if(!control.includes(p.request_control))issues.push('capabilities.reasoning_profile.request_control：此适配不支持所选开关。');
 if(!['provider_default','enabled','disabled','auto'].includes(p.mode)||p.request_control==='none'&&p.mode!=='provider_default'||p.mode==='auto'&&(!p.profile_id.startsWith('ark_')||p.request_control!=='thinking_type'))issues.push('capabilities.reasoning_profile.mode：所选模式不受此版本适配与开关组合支持。');
 return issues;
}
export function reasoningBlockedParameters(value:unknown):string[]{const p=value as Partial<ReasoningProfile>|undefined;return p?.profile_id==='deepseek_public_reasoning_v1'&&p.mode!=='disabled'?['temperature','top_p','frequency_penalty','presence_penalty']:[];}
export function modelParameterAllowed(model:ModelChoice|undefined,key:string){return !!model&&model.capabilities.parameters.includes(key)&&!reasoningBlockedParameters(model.capabilities.reasoning_profile).includes(key);}
export function assertModelReasoningProfile(value:unknown,modelId:string,contractSupported:boolean,parameters:string[],tools:boolean){if(value==null)return;const issues=reasoningProfileIssues(value,modelId);if(!contractSupported)issues.push('当前服务尚未确认公开推理适配配置字段，本次修改未保存。');const blocked=reasoningBlockedParameters(value).filter(key=>parameters.includes(key));if(blocked.length)issues.push(`capabilities.parameters：请明确移除本适配未放行的参数声明：${blocked.map(k=>parameterLabels[k]).join('、')}。`);if(tools)issues.push('capabilities.tools：此公开推理适配尚不支持工具多轮，请明确关闭工具调用声明。');if(issues.length)throw Error(issues.join(' '));}
export function reasoningProfileContract(value:any):boolean{
 const schemas=value?.components?.schemas||{},resolve=(v:any)=>v?.$ref?schemas[v.$ref.split('/').at(-1)]:v;
 const route=value?.paths?.['/api/v1/model-providers/{provider_id}/models']?.post,write=resolve(route?.requestBody?.content?.['application/json']?.schema),caps=resolve(write?.properties?.capabilities),raw=caps?.properties?.reasoning_profile,profile=resolve(raw?.anyOf?.find((v:any)=>v.$ref)||raw),p=profile?.properties;
 return profile?.additionalProperties===false&&Object.keys(p||{}).sort().join(',')==='mode,model_id,model_revision,profile_id,protocol,request_control,transport'&&Array.isArray(p?.profile_id?.enum)&&Object.keys(reasoningProfileNames).every(k=>p.profile_id.enum.includes(k))&&p?.protocol?.const==='openai_chat_completions'&&p?.model_id?.type==='string'&&p?.model_revision?.type==='string'&&p?.transport?.enum?.includes('json')&&p?.transport?.enum?.includes('sse')&&p?.request_control?.enum?.includes('none')&&p?.mode?.enum?.includes('provider_default');
}

export function visionProfileIssues(value:unknown,modelId?:string,reasoning?:unknown):string[]{
 if(value==null)return [];const p=value as Record<string,any>,fields='input_source,model_id,model_revision,profile_id,protocol,token_reservation';
 if(typeof p!=='object'||Array.isArray(p)||Object.keys(p).sort().join(',')!==fields)return ['capabilities.vision_profile：图片输入声明须为明确的六字段对象，原值保留，请明确移除或重新声明。'];
 const issues:string[]=[];
 if(p.profile_id!=='openai_image_url_v1'||p.protocol!=='openai_chat_completions')issues.push('capabilities.vision_profile：当前仅支持Chat Completions兼容图片输入适配。');
 if(p.input_source!=='current_run_message_attachment')issues.push('capabilities.vision_profile.input_source：图片来源仅限本次运行已受理消息附件。');
 if(p.token_reservation!=='tiles512_2048_plus1024_v1')issues.push('capabilities.vision_profile.token_reservation：请使用当前受控图片预算策略。');
 if(typeof p.model_id!=='string'||!p.model_id.trim()||p.model_id.length>200||modelId!==undefined&&p.model_id!==modelId)issues.push('capabilities.vision_profile.model_id：图片适配须与服务实际model名称精确一致，请明确重新绑定。');
 if(typeof p.model_revision!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$/.test(p.model_revision)||['latest','auto','default','unknown'].includes(p.model_revision.toLowerCase()))issues.push('capabilities.vision_profile.model_revision：请填写具体模型版本，不能使用latest/auto/default/unknown漂移标签。');
 if(reasoning!=null){const r=reasoning as Partial<ReasoningProfile>;if(reasoningProfileIssues(reasoning).length||r.profile_id==='deepseek_public_reasoning_v1'||r.model_id!==p.model_id||r.model_revision!==p.model_revision)issues.push('capabilities.vision_profile：与公开推理联用须绑定同一实际model和具体版本；当前DeepSeek公开推理适配未开放图片联用。');}
 return issues;
}
export function assertModelVisionProfile(value:unknown,modelId:string,supported:boolean,reasoning?:unknown){if(value==null)return;const issues=visionProfileIssues(value,modelId,reasoning);if(!supported)issues.push('当前服务尚未确认图片输入适配配置字段，本次修改未保存。');if(issues.length)throw Error(issues.join(' '));}
export function visionProfileContract(value:any):boolean{
 const schemas=value?.components?.schemas||{},resolve=(v:any)=>v?.$ref?schemas[v.$ref.split('/').at(-1)]:v,route=value?.paths?.['/api/v1/model-providers/{provider_id}/models']?.post,write=resolve(route?.requestBody?.content?.['application/json']?.schema),caps=resolve(write?.properties?.capabilities),raw=caps?.properties?.vision_profile,profile=resolve(raw?.anyOf?.find((v:any)=>v.$ref)||raw),p=profile?.properties;
 return profile?.additionalProperties===false&&Object.keys(p||{}).sort().join(',')==='input_source,model_id,model_revision,profile_id,protocol,token_reservation'&&p?.profile_id?.const==='openai_image_url_v1'&&p?.protocol?.const==='openai_chat_completions'&&p?.model_id?.type==='string'&&p?.model_revision?.type==='string'&&p?.model_revision?.pattern==='^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$'&&p?.input_source?.const==='current_run_message_attachment'&&p?.token_reservation?.const==='tiles512_2048_plus1024_v1';
}
