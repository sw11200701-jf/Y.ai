import { timedLoad } from './load-timing';

/** Share successful code/prefetch reads, but never retain a rejected or timed-out attempt. */
export function createWorkflowCodeLoad<T>(read:()=>Promise<T>,timeoutMs=15000){
  let pending:Promise<T>|undefined;
  return ()=>{
    if(pending)return pending;
    const attempt=timedLoad('code',()=>new Promise<T>((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('画布组件读取超时')),timeoutMs);
      Promise.resolve().then(read).then(
        value=>{clearTimeout(timer);resolve(value);},
        error=>{clearTimeout(timer);reject(error);},
      );
    }));
    pending=attempt;
    void attempt.catch(()=>{if(pending===attempt)pending=undefined;});
    return attempt;
  };
}
