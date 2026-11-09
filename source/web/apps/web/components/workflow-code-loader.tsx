'use client';
import { useEffect, useState, type ComponentType } from 'react';
import { Button, Skeleton, Space } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';
import { createWorkflowCodeLoad } from '@/lib/workflow-code-load';
import type { WorkflowStudioProps } from '@/lib/workflow/types';

export const loadWorkflowStudio=createWorkflowCodeLoad(()=>import('./workflow-studio'));
type Loaded={identity:string|undefined;component?:ComponentType<WorkflowStudioProps>;failed?:boolean};

/** An explicit import attempt avoids React.lazy retaining a failed chunk Promise. */
export default function WorkflowCodeLoader({onCancelLoading,...props}:WorkflowStudioProps&{onCancelLoading:()=>void}){
  const [attempt,setAttempt]=useState(0),[loaded,setLoaded]=useState<Loaded>();
  useEffect(()=>{
    let active=true;
    const epoch=currentIdentityEpoch(),identity=props.identityKey;
    setLoaded(undefined);
    void loadWorkflowStudio().then(
      module=>{if(active&&epoch===currentIdentityEpoch())setLoaded({identity,component:module.default});},
      ()=>{if(active&&epoch===currentIdentityEpoch())setLoaded({identity,failed:true});},
    );
    return()=>{active=false;};
  },[attempt,props.identityKey]);
  const state=loaded?.identity===props.identityKey?loaded:undefined;
  const Studio=state?.component;
  if(Studio)return <Studio {...props}/>;
  return <section className="workflow-code-loading" role={state?.failed?'alert':'status'}>
    <Button onClick={onCancelLoading}>返回AI工作室</Button>
    <h2>{state?.failed?'画布组件暂时无法载入':'正在载入画布组件'}</h2>
    {state?.failed?<><p>网络恢复后可重试加载。若仍失败，请重新加载页面；未保存内容不会自动提交。</p><Space wrap>
      <Button type="primary" onClick={()=>{setLoaded(undefined);setAttempt(value=>value+1);}}>重试加载画布</Button>
      <Button onClick={()=>window.location.reload()}>重新加载页面</Button>
    </Space></>:<Skeleton active paragraph={{rows:4}}/>}
  </section>;
}
