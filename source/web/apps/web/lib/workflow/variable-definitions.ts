import type {WorkflowDefinition,ObjectValue} from './types';
const copy=<T,>(x:T):T=>JSON.parse(JSON.stringify(x));
export function variableReferences(workflow:WorkflowDefinition,name:string):string[]{const found:string[]=[];const prefix=`vars.${name}`;const visit=(value:any,path:string)=>{if(!value||typeof value!=='object'||path&&Array.isArray(value.nodes))return;if(typeof value.$ref==='string'&&(value.$ref===prefix||value.$ref.startsWith(prefix+'.')))found.push(path);if(value.type==='assign'&&value.config?.name===name)found.push(path+'.config.name');for(const [key,child] of Object.entries(value))if(key!=='variables')visit(child,path?`${path}.${key}`:key);};visit(workflow,'');return found;}
export function redefineVariables(workflow:WorkflowDefinition,next:ObjectValue):WorkflowDefinition{
 const old=workflow.variables||{},removed=Object.keys(old).filter(k=>!Object.hasOwn(next,k)),added=Object.keys(next).filter(k=>!Object.hasOwn(old,k));const result=copy(workflow);
 if(removed.length===1&&added.length===1&&JSON.stringify(old[removed[0]])===JSON.stringify(next[added[0]])){
  const from=removed[0],to=added[0],prefix=`vars.${from}`;const visit=(value:any,root=false)=>{if(!value||typeof value!=='object'||!root&&Array.isArray(value.nodes))return;if(typeof value.$ref==='string'&&(value.$ref===prefix||value.$ref.startsWith(prefix+'.')))value.$ref=`vars.${to}`+value.$ref.slice(prefix.length);if(value.type==='assign'&&value.config?.name===from)value.config.name=to;for(const [key,child] of Object.entries(value))if(key!=='variables')visit(child);};visit(result,true);
 }else for(const name of removed){const refs=variableReferences(workflow,name);if(refs.length)throw Error(`变量“${name}”仍被 ${refs.length} 处引用，请先修改引用后删除。`);}
 for(const name of Object.keys(next))if(old[name]&&JSON.stringify(old[name].type)!==JSON.stringify(next[name].type)&&variableReferences(workflow,name).length)throw Error(`变量“${name}”仍被引用，修改类型前请先更新或移除引用。`);
 result.variables=copy(next);return result;
}
