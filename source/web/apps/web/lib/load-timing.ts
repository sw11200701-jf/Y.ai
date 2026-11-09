/** Local Performance entries contain stage labels and durations only, never resource names or credentials. */
export async function timedLoad<T>(stage:'code'|'assets'|'configuration'|'registry'|'resources'|'workflow'|'owner',read:()=>Promise<T>):Promise<T>{
 const clock=()=>typeof performance!=='undefined'?performance.now():Date.now(),start=clock();
 try{return await read();}finally{if(typeof performance!=='undefined')try{performance.measure(`zhangpu-load:${stage}`,{start,end:clock()});}catch{/* Older browsers still complete the read. */}}
}

export const loadClock=()=>typeof performance!=='undefined'?performance.now():Date.now();
export function recordLoad(stage:'editable'|'render_commit',start:number){
 if(typeof performance!=='undefined')try{if(performance.getEntriesByName(`zhangpu-load:${stage}`).length>=30)performance.clearMeasures(`zhangpu-load:${stage}`);performance.measure(`zhangpu-load:${stage}`,{start,end:loadClock()});}catch{/* Timing is diagnostic, never part of product success. */}
}
