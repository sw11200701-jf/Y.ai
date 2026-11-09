import type {WorkflowDefinition,StudioEvent,NodeTrace} from './types';
export function orderedRunNodes(workflow:WorkflowDefinition,events:StudioEvent[],traces:NodeTrace[]=[]){
 const nodes=new Map(workflow.nodes.map(n=>[n.id,n])),seen=new Set<string>(),ids:string[]=[];
 const add=(id?:string|null)=>{if(id&&nodes.has(id)&&!seen.has(id)){seen.add(id);ids.push(id);}};
 events.filter(e=>e.type==='node.started').slice().sort((a,b)=>a.sequence-b.sequence).forEach(e=>add(e.node_id));
 traces.filter(t=>t.started_at).slice().sort((a,b)=>String(a.started_at).localeCompare(String(b.started_at))).forEach(t=>add(t.node_id));
 const degree=new Map(workflow.nodes.map(n=>[n.id,workflow.edges.filter(e=>e.target===n.id).length]));const queue=workflow.nodes.filter(n=>degree.get(n.id)===0).map(n=>n.id);
 for(let i=0;i<queue.length;i++){const id=queue[i];add(id);for(const edge of workflow.edges.filter(e=>e.source===id)){degree.set(edge.target,(degree.get(edge.target)||0)-1);if(degree.get(edge.target)===0)queue.push(edge.target);}}
 workflow.nodes.forEach(n=>add(n.id));return ids.map(id=>nodes.get(id)!);
}
