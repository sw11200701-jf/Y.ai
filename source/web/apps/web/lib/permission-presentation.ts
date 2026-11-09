import {menuLabels,permissionLabel} from './ui-labels';
export type PermissionItem={key:string;name:string};
export type PermissionColumn='page'|'manage'|'special';
export const permissionColumnLabels:Record<PermissionColumn,string>={page:'页面访问',manage:'管理权限',special:'专项操作'};
const layout:{id:string;module:keyof typeof menuLabels;detail?:string;cells:Partial<Record<PermissionColumn,string>>}[]=[
 {id:'workbench',module:'workbench',cells:{page:'page.workbench'}},
 {id:'agents',module:'agents',cells:{page:'page.ai'}},
 {id:'knowledge',module:'data',detail:'知识库',cells:{page:'page.knowledge'}},
 {id:'database',module:'data',detail:'数据库',cells:{page:'page.database'}},
 {id:'plugins',module:'plugins',cells:{page:'page.plugins'}},
 {id:'models',module:'models',cells:{page:'page.models',manage:'models.manage'}},
 {id:'runs',module:'runs',cells:{page:'page.runs',special:'runs.approve'}},
 {id:'applications',module:'applications',cells:{page:'page.applications',manage:'applications.manage'}},
 {id:'accounts',module:'accounts',cells:{page:'page.accounts',manage:'accounts.manage'}},
 {id:'roles',module:'roles',cells:{page:'page.roles',manage:'roles.manage'}},
];
export function permissionPresentation(catalog:PermissionItem[],selected:readonly string[]=[]){
 const byKey=new Map(catalog.map(item=>[item.key,item]));const known=new Set<string>();
 const rows=layout.flatMap(row=>{const cells:Partial<Record<PermissionColumn,PermissionItem>>={};for(const column of ['page','manage','special'] as const){const key=row.cells[column];if(key){known.add(key);const item=byKey.get(key);if(item)cells[column]=item;}}return Object.keys(cells).length?[{...row,title:menuLabels[row.module],cells}]:[];});
 const columns=(['page','manage','special'] as const).filter(column=>rows.some(row=>row.cells[column]));
 return {rows,columns,other:[...byKey.values()].filter(item=>!known.has(item.key)),missing:selected.filter(key=>!byKey.has(key))};
}
export function permissionSummaries(selected:readonly string[],catalog:PermissionItem[]){
 const {rows,other,missing}=permissionPresentation(catalog,selected);const summaries:{id:string;label:string;details:string[]}[]=[];
 for(const row of rows){const enabled=Object.entries(row.cells).filter(([,item])=>selected.includes(item.key));if(!enabled.length)continue;
  let summary=summaries.find(item=>item.id===row.module);if(!summary){summary={id:row.module,label:row.title,details:[]};summaries.push(summary);}
  summary.details.push(...enabled.map(([column,item])=>row.detail?`${row.detail} · ${permissionColumnLabels[column as PermissionColumn]}`:item.key==='runs.approve'?'审批指定运行':permissionColumnLabels[column as PermissionColumn]));
 }
 const extra=[...other.filter(item=>selected.includes(item.key)).map(item=>permissionLabel(item.key,item.name)),...missing];
 if(extra.length)summaries.push({id:'other',label:'其他权限',details:extra});
 return summaries;
}
