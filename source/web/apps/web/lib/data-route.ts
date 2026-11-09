export type DataRoute={section:'knowledge'|'databases';resource?:string;table?:string;tab?:string};
export function readDataRoute(search:string):DataRoute{const p=new URLSearchParams(search);return {section:p.get('section')==='databases'?'databases':'knowledge',resource:p.get('resource')||undefined,table:p.get('table')||undefined,tab:p.get('tab')||undefined};}
export function dataRouteUrl(route:DataRoute){const p=new URLSearchParams({page:'data',section:route.section});for(const key of ['resource','table','tab'] as const)if(route[key])p.set(key,route[key]!);return '?'+p.toString();}
