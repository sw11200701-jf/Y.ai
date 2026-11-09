import type {Json} from './api';
export function conversationChoices(items:Json[]):Json[]{return items.filter(item=>item.conversation_ready===true&&typeof item.conversation_version_id==='string'&&item.configuration_status!=='unsupported_legacy_skill_bindings'&&!item.builtin&&!item.main_workflow_id);}

export function conversationScope(user:Json):string{return `zhangpu:conversation-choice:v1:${JSON.stringify([user.tenant_id||'',user.workspace_id||'',user.id||''])}`;}
export function chooseConversation(items:Json[],preferred?:string|null):string|undefined {const available=conversationChoices(items);return available.find(item=>item.id===preferred)?.id||available[0]?.id;}
export function rememberedConversation(user:Json):string|undefined {try{return window.localStorage.getItem(conversationScope(user))||undefined;}catch{return undefined;}}
export function rememberConversation(user:Json,id?:string):void {try{if(id)window.localStorage.setItem(conversationScope(user),id);else window.localStorage.removeItem(conversationScope(user));}catch{/* Storage restrictions do not prevent manual conversation. */}}
