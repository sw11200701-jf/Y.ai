'use client';
import {useEffect,useState} from 'react';
import {request} from './api';

export function useDataActions(kind:string|undefined,id:string|undefined){
 const [actions,setActions]=useState<string[]>([]);
 useEffect(()=>{let active=true;setActions([]);if(kind&&id)void request(`/data-access/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/capabilities`).then(value=>{if(active&&Array.isArray(value.available_actions))setActions(value.available_actions.filter((item:unknown):item is string=>typeof item==='string'));}).catch(()=>{if(active)setActions([]);});return()=>{active=false;};},[kind,id]);
 return actions;
}
