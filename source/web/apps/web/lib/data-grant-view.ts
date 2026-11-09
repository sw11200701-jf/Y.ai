import type { Json } from './api';
const actions=new Set(['use','maintain','manage','download_original','export_detail']);
const object=(value:unknown):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value);
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(item=>typeof item==='string');
function invalid():never{throw Object.assign(new Error('数据授权资料格式不完整，请重新读取后再编辑。'),{code:'UI_RESPONSE_INVALID'});}
export function dataGrantView(response:unknown):{metadata:Json;rules:Json[]}{
  if(!object(response)||!Array.isArray(response.grants))invalid();
  const rules=response.grants.map(grant=>{
    if(!object(grant)||!['user','role'].includes(grant.subject_type)||typeof grant.subject_id!=='string'||!['allow','deny','restrict'].includes(grant.effect)||!strings(grant.actions)||grant.actions.some(action=>!actions.has(action)))invalid();
    const row=grant.row_scope;
    const columns=grant.columns;
    if(!object(row)||!['all','self','department','companies','departments'].includes(row.template)||!strings(row.ids)||row.field!=null&&typeof row.field!=='string')invalid();
    if(!object(columns)||columns.allow!==null&&!strings(columns.allow)||!strings(columns.hide)||!object(columns.mask)||Object.values(columns.mask).some(value=>!['redact','last4'].includes(value)))invalid();
    const delegated=grant.delegable_actions;if(!strings(delegated)||delegated.some(action=>!actions.has(action)))invalid();
    return {subject_type:grant.subject_type,subject_id:grant.subject_id,effect:grant.effect,actions:[...grant.actions],row_scope:{...row,ids:[...row.ids]},columns:{...columns,allow:columns.allow===null?null:[...columns.allow],hide:[...columns.hide],mask:{...columns.mask}},delegable_actions:[...delegated]};
  });
  return {metadata:{kind:response.kind,resource_id:response.resource_id,owner_id:response.owner_id,policy_version:response.policy_version,legacy_grants_preserved:response.legacy_grants_preserved},rules};
}
