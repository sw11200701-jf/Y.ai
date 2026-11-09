'use client';
import {useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import styles from './workspace-navigation.module.css';

type Item={id:string;title:string;icon:ReactNode};
type Position={x:number;y:number;width:number;height:number};
export default function WorkspaceNavigation({items,selected,onNavigate}:{items:Item[];selected:string;onNavigate:(id:string)=>void}){
 const nav=useRef<HTMLElement>(null),[position,setPosition]=useState<Position>(),[armed,setArmed]=useState(false);
 const signature=items.map(item=>item.id).join('|');
 useLayoutEffect(()=>{
  const root=nav.current;if(!root)return;
  const active=Array.from(root.querySelectorAll<HTMLButtonElement>('button[data-page]')).find(button=>button.dataset.page===selected);
  if(!active){setPosition(undefined);return;}
  let live=true;
  const measure=()=>{if(!live)return;if(!active.offsetWidth||!active.offsetHeight){setPosition(undefined);return;}const next={x:active.offsetLeft,y:active.offsetTop,width:active.offsetWidth,height:active.offsetHeight};setPosition(previous=>previous&&Object.keys(next).every(key=>previous[key as keyof Position]===next[key as keyof Position])?previous:next);};
  measure();const frame=requestAnimationFrame(()=>{if(live)setArmed(true);});
  const observer=typeof ResizeObserver==='undefined'?undefined:new ResizeObserver(measure);observer?.observe(root);observer?.observe(active);window.addEventListener('resize',measure);
  // Scroll only the menu's own horizontal viewport, never the page or editor.
  if(root.scrollWidth>root.clientWidth&&(active.offsetLeft<root.scrollLeft||active.offsetLeft+active.offsetWidth>root.scrollLeft+root.clientWidth))root.scrollTo({left:Math.max(0,active.offsetLeft-(root.clientWidth-active.offsetWidth)/2),behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  return()=>{live=false;cancelAnimationFrame(frame);observer?.disconnect();window.removeEventListener('resize',measure);};
 },[selected,signature]);
 return <nav ref={nav} className={styles.navigation} aria-label="工作空间菜单" data-animated={armed?'true':'false'}>
  {position&&<span aria-hidden="true" className={styles.indicator} style={{transform:`translate3d(${position.x}px,${position.y}px,0)`,width:position.width,height:position.height}}/>}
  {items.map(item=><button key={item.id} type="button" data-page={item.id} aria-current={selected===item.id?'page':undefined} className={`nav-item ${styles.item}${selected===item.id?' active':''}`} onClick={()=>onNavigate(item.id)}>{item.icon}<span>{item.title}</span>{selected===item.id&&<span className={`nav-dot ${styles.dot}`} aria-hidden="true"/>}</button>)}
 </nav>;
}
