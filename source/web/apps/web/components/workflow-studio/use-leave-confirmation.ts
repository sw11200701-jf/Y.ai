'use client';
import {createElement,useCallback,useEffect,useRef,type ReactNode} from 'react';

type Dialog={destroy:()=>void};
type Ticket={request:(allowed:boolean,override?:boolean)=>void;abort:()=>void;dialog?:Dialog};
type Confirmation={title:string;content:ReactNode;centered:boolean;okText:string;cancelText:string;destroyOnHidden:true;getContainer:()=>HTMLElement;onOk:(close?:()=>void)=>void;onCancel:(close?:()=>void)=>void;afterClose:()=>void};
type Options={dirty:()=>boolean;blocked:()=>boolean;container:()=>HTMLElement;show:(options:Confirmation)=>Dialog|undefined;onError:()=>void};

/** Passive unmount runs after the portal's layout cleanup releases its own scroll lock. */
function ConfirmationBody({onMount,onRelease}:{onMount:()=>void;onRelease:()=>void}){
 useEffect(()=>{onMount();return onRelease;},[onMount,onRelease]);
 return createElement('span',null,'离开将放弃本次修改。你可以继续编辑，保存后再返回。');
}

/** Keep one ticket until its own dialog has closed AND its portal content has unmounted. */
export function useLeaveConfirmation(options:Options){
 const latest=useRef(options);latest.current=options;const mounted=useRef(true),pending=useRef<Ticket|null>(null);
 const dismiss=useCallback(()=>pending.current?.request(false,true),[]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;pending.current?.abort();};},[]);
 const guard=useCallback(():boolean|Promise<boolean>=>{
  if(!mounted.current||latest.current.blocked()||pending.current)return false;
  if(!latest.current.dirty())return true;
  return new Promise<boolean>(resolve=>{
   let decision:boolean|undefined,closed=false,released=false,destroyed=false;
   const ticket:Ticket={request:()=>{},abort:()=>{}};
   const settle=(allowed:boolean)=>{if(pending.current!==ticket)return;pending.current=null;resolve(allowed&&mounted.current);};
   const finish=()=>{if(closed&&released)settle(decision===true);};
   const destroy=()=>{if(ticket.dialog&&!destroyed){destroyed=true;ticket.dialog.destroy();}};
   ticket.request=(allowed,override=false)=>{if(pending.current!==ticket)return;if(decision===undefined||override)decision=allowed;destroy();};
   ticket.abort=()=>{ticket.request(false,true);settle(false);};
   pending.current=ticket;
   try{
    // Returning root.current later could reparent a closing portal to body after route teardown.
    const container=latest.current.container();
    ticket.dialog=latest.current.show({title:'工作流尚未保存',content:createElement(ConfirmationBody,{onMount:()=>{released=false;},onRelease:()=>{released=true;finish();}}),centered:true,okText:'放弃修改并离开',cancelText:'继续编辑',destroyOnHidden:true,getContainer:()=>container,
     // One-argument callbacks leave closing to this ticket's handle, not ActionButton auto-close.
     onOk:(_close?:()=>void)=>ticket.request(true),onCancel:(_close?:()=>void)=>ticket.request(false),afterClose:()=>{closed=true;finish();}});
    if(!ticket.dialog){settle(false);latest.current.onError();return;}
    if(decision!==undefined||pending.current!==ticket)destroy();
   }catch{settle(false);latest.current.onError();}
  });
 },[]);
 return {guard,dismiss};
}
