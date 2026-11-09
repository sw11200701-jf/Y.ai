'use client';
import type {Json} from '@/lib/api';
const labels:Record<string,string>={document_version_id:'文档版本',window_id:'处理窗口',units:'原文单元',unit_id:'原文单元标识',text:'原文',overview:'窗口概述',topics:'主题列表',chunks:'语义切片',start:'起始字符位置',end:'结束字符位置',quote:'原文引用',topic:'切片主题',summary:'切片概述'};
const types:Record<string,string>={string:'文本',integer:'整数',number:'数字',boolean:'是/否',object:'对象',array:'列表',null:'空值'};
function resolved(schema:Json,root:Json):Json{if(typeof schema.$ref!=='string')return schema;const match=/^#\/\$defs\/([^/]+)$/.exec(schema.$ref);return match&&root.$defs?.[match[1]]||schema;}
function bounds(schema:Json){return [['minLength','最少字数'],['maxLength','最多字数'],['minItems','最少项数'],['maxItems','最多项数'],['minimum','最小值'],['maximum','最大值']].filter(([key])=>schema[key]!==undefined).map(([key,label])=>`${label} ${schema[key]}`).join(' · ');}
export default function BuiltinAgentSchema({schema,root=schema,depth=0}:{schema:Json;root?:Json;depth?:number}){
 const value=resolved(schema,root);if(depth>=6)return <span>更深层结构由服务端固定。</span>;
 if(value.type==='array')return <div>列表{bounds(value)&&`（${bounds(value)}）`}<BuiltinAgentSchema schema={value.items||{}} root={root} depth={depth+1}/></div>;
 if(value.type!=='object')return <span>{types[value.type]||'固定复合类型'}{bounds(value)&&`（${bounds(value)}）`}</span>;
 return <ul style={{paddingLeft:20}}>{Object.entries(value.properties||{}).map(([key,raw])=>{const field=resolved(raw as Json,root),nested=['object','array'].includes(field.type);return <li key={key} style={{margin:'8px 0'}}><strong>{labels[key]||field.title||key}</strong>{value.required?.includes(key)?' · 必填':''}{field.description&&<p>{field.description}</p>}{nested?<details><summary>{types[field.type]} · 展开固定结构</summary><BuiltinAgentSchema schema={field} root={root} depth={depth+1}/></details>:<> · <BuiltinAgentSchema schema={field} root={root} depth={depth+1}/></>}</li>;})}</ul>;
}
