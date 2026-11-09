import { emptyMessage, messageKinds, toMessageEnvelope } from './messages';
import type { MessageDraft, MessageKind, NodeRegistry, WorkflowDefinition } from './types';
export function newMessageWorkflow(registry:NodeRegistry,kind:MessageKind):WorkflowDefinition {
 const entry=registry.items.find(node=>node.type===`trigger_${kind}`&&node.status==='supported'),output=registry.items.find(node=>node.type==='output'&&node.status!=='unavailable'),label=messageKinds.find(item=>item.type===kind)?.label;
 if(!label||!entry||!output)throw Error('所选消息入口或输出尚未开放，请刷新能力目录');
 const id=`message_${kind}`;
 const properties:Record<string,any>={text:{type:'string'}};
 if(kind!=='text')properties.attachment_ids={type:'array',items:{type:'string'}};
 const schema={type:'object',properties,required:Object.keys(properties),additionalProperties:false};
 return {name:`${label}工作流`,version:'1.0.0',schemaVersion:'2.0',mode:'demo',ui:{field_contract:'business_v1'},input_schema:schema,variables:{},limits:{max_steps:256,timeout_seconds:120},nodes:[{id,type:entry.type,config:{message_field:'text',attachments_field:'attachment_ids'},inputs:{},ui:{position:{x:80,y:180}}},{id:'output',type:'output',input_schema:schema,output_schema:schema,config:{presentation:kind==='text'?'text':'json',output_mapping:Object.fromEntries(Object.keys(properties).map(k=>[k,{$ref:`fields.${k}`}]))},inputs:Object.fromEntries(Object.keys(properties).map(k=>[k,{$ref:`nodes.${id}.${k}`}])) ,ui:{position:{x:420,y:180}}}],edges:[{source:id,target:'output',source_port:'default',target_port:'default'}]};
}
export function initialWorkflowMessage(workflow:WorkflowDefinition|null):MessageDraft {
 const entries=workflow?.nodes.filter(node=>messageKinds.some(kind=>node.type===`trigger_${kind.type}`))||[];
 const kind=entries.length===1?messageKinds.find(kind=>entries[0].type===`trigger_${kind.type}`)?.type:'text';
 return {...emptyMessage(),type:kind||'text'};
}
/** Only message-entry workflows ask for a message; business starts use their declared fields. */
export function workflowTestInputLayout(workflow:WorkflowDefinition,message:MessageDraft,embedded=false) {
 const typed=workflow.nodes.filter(node=>messageKinds.some(kind=>node.type===`trigger_${kind.type}`));
 const useMessage=typed.length>0||(embedded&&workflow.ui?.field_contract!=='business_v1');
 const entry=typed.find(node=>node.type===`trigger_${message.type}`);
 const fields=entry?[String(entry.config?.message_field||'text'),String(entry.config?.attachments_field||'attachment_ids')]:[];
 const schema=workflow.input_schema||{type:'object',properties:{}};
 return {useMessage,schema:{...schema,properties:Object.fromEntries(Object.entries(schema.properties||{}).filter(([name])=>!fields.includes(name))),
   ...(Array.isArray(schema.required)?{required:schema.required.filter(name=>!fields.includes(name))}:{})}};
}
/** Main and node tests share the same entry rule; hidden stale messages are never submitted. */
export function workflowMessageEnvelope(workflow:WorkflowDefinition,message:MessageDraft,embedded=false) {
 return workflowTestInputLayout(workflow,message,embedded).useMessage?toMessageEnvelope(message):undefined;
}
