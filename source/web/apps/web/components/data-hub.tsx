'use client';
import {useEffect,useState} from 'react';
import {Segmented} from 'antd';
import {hasPermission} from '@/lib/permissions';
import {dataRouteUrl,readDataRoute,type DataRoute} from '@/lib/data-route';
import {useSession} from './auth-gate';
import DataCenter from './data-center';
import DatabaseCenter from './database-center';
import {ErrorBanner} from './shared';
export default function DataHub(){const {session}=useSession();const [route,setRoute]=useState<DataRoute>({section:'knowledge'});const [ready,setReady]=useState(false);const options=[...(hasPermission(session.user,'page.knowledge')?[{value:'knowledge',label:'知识库'}]:[]),...(hasPermission(session.user,'page.database')?[{value:'databases',label:'数据库'}]:[])];
 useEffect(()=>{const read=()=>{const next=readDataRoute(location.search);if(!new URLSearchParams(location.search).has('section')&&!hasPermission(session.user,'page.knowledge'))next.section='databases';setRoute(next);setReady(true);};read();window.addEventListener('popstate',read);return()=>window.removeEventListener('popstate',read);},[]);
 function navigate(next:DataRoute){setRoute(next);history.pushState({},'',dataRouteUrl(next));}
 return <><div className="data-center-switch"><Segmented aria-label="数据中心类型" options={options} value={route.section} onChange={section=>navigate({section:section as DataRoute['section']})}/></div>{ready&&(hasPermission(session.user,route.section==='knowledge'?'page.knowledge':'page.database')?(route.section==='knowledge'?<DataCenter key="knowledge" route={route} onRouteChange={navigate}/>:<DatabaseCenter key="databases" route={route} onRouteChange={navigate}/>):<ErrorBanner error="当前账号没有此类数据的管理页面权限。"/>)}</>;
}
