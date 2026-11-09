'use client';
import WorkspaceNavigation from './workspace-navigation';
import motion from './workspace-navigation.module.css';
import { menuLabels } from '@/lib/ui-labels';
import { Fragment, useEffect, useRef, useState } from 'react';
import { Alert, App, Badge, Button, Tag } from 'antd';
import { DashboardOutlined, DatabaseOutlined, FileTextOutlined, LinkOutlined, LogoutOutlined, ReloadOutlined, RobotOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons';
import { currentIdentityEpoch, Json, request, sanitizeAccessDenial, type AccessDenialDiagnostic } from '@/lib/api';
import { createNavigationGate } from '@/lib/navigation';
import AuthGate, { useSession } from './auth-gate';
import HomeWorkspace from './home-workspace';
import Workbench from './workbench';
import DataHub from './data-hub';
import PluginCenter from './plugin-center';
import Agents from './agents';
import Runs from './runs';
import ApplicationApiPage from './application-api-page';
import ApprovalInbox from './approval-inbox';
import Accounts from './accounts';
import Roles from './roles';
import BuiltinAgents from './builtin-agents';
import ModelManagement from './model-management';
import PlatformAccess from './platform-access';
import AssetApiAccess from './asset-api-access';
import PageErrorBoundary from './page-error-boundary';
import { canOpenPage } from '@/lib/permissions';
import { errorMessage } from '@/lib/errors';
const pages = [
  { id: 'workbench', title: menuLabels.workbench, icon: <DashboardOutlined /> },
  { id: 'agents', title: menuLabels.agents, icon: <RobotOutlined /> },
  { id: 'data', title: menuLabels.data, icon: <DatabaseOutlined /> },
  { id: 'plugins', title: menuLabels.plugins, icon: <LinkOutlined /> },
  { id: 'models', title: menuLabels.models, icon: <RobotOutlined /> },
  {id:'platform-access',title:'平台 API 与空间',icon:<LinkOutlined/>},
  {id:'asset-api',title:'API 接入配置',icon:<LinkOutlined/>},
  { id: 'applications', title: menuLabels.applications, icon: <LinkOutlined /> },
  { id: 'runs', title: menuLabels.runs, icon: <FileTextOutlined /> },
  { id: 'approvals', title: menuLabels.approvals, icon: <SafetyCertificateOutlined /> },
  { id: 'accounts', title: menuLabels.accounts, icon: <UserOutlined /> },
  { id: 'chunker-settings', title:'知识切片配置', icon:<RobotOutlined/> },
  { id: 'roles', title: menuLabels.roles, icon: <SafetyCertificateOutlined /> },
];
export default function Console() { return <AuthGate><ConsoleBody /></AuthGate>; }
function ConsoleBody() {
  const { session, logout, permissionsChecking } = useSession();
  const committedUrl=useRef('');const navigation=useRef(createNavigationGate(currentIdentityEpoch));
  const [dataEpoch,setDataEpoch]=useState(0);
  const [accessLost,setAccessLost]=useState(false);
  const [accessDiagnostic,setAccessDiagnostic]=useState<AccessDenialDiagnostic>();
  useEffect(()=>{const denied=(event:Event)=>{navigation.current.cancel();setAccessDiagnostic(sanitizeAccessDenial((event as CustomEvent).detail));setAccessLost(true);};const clear=()=>setAccessDiagnostic(undefined);window.addEventListener('zhangpu:access-denied',denied);window.addEventListener('zhangpu:session-expired',clear);window.addEventListener('zhangpu:permissions-changed',clear);return()=>{window.removeEventListener('zhangpu:access-denied',denied);window.removeEventListener('zhangpu:session-expired',clear);window.removeEventListener('zhangpu:permissions-changed',clear);};},[]);
  useEffect(()=>setAccessDiagnostic(undefined),[session.user.id]);
  useEffect(()=>{const changed=()=>{if(new URLSearchParams(location.search).get('page')!=='accounts')setDataEpoch(value=>value+1);};window.addEventListener('zhangpu:data-policy-changed',changed);return()=>window.removeEventListener('zhangpu:data-policy-changed',changed);},[]);
  const [page, setPage] = useState('workbench');const [canvas,setCanvas]=useState(false); const [cap, setCap] = useState<Json>(); const [error, setError] = useState(''); const [checking, setChecking] = useState(true);
  async function load() { setChecking(true); setError(''); try { setCap(await request('/capabilities')); } catch(e) { setError(errorMessage(e)); } finally { setChecking(false); } }
  function readRoute(target:string){setAccessDiagnostic(undefined);const query=new URLSearchParams(target.split('?')[1]||'');const p=query.get('page');setCanvas(p==='agents'&&query.get('view')==='canvas');setPage(p==='workflows'?'agents':p==='finance-sample'||pages.some(x=>x.id===p)?p!:'workbench');committedUrl.current=target;}
  async function checkLeave(){const checks:Promise<unknown>[]=[];const event=new CustomEvent('zhangpu:before-navigation',{cancelable:true,detail:{waitUntil:(result:unknown)=>checks.push(Promise.resolve(result))}});if(!window.dispatchEvent(event))return false;return (await Promise.all(checks)).every(allowed=>allowed===true);}
  function acceptRoute(target:string,replace=false){history[replace?'replaceState':'pushState']({},'',target);readRoute(target);setAccessLost(false);window.dispatchEvent(new CustomEvent('zhangpu:route-accepted',{detail:{url:target}}));}
  useEffect(() => {
    const read=()=>readRoute((location.pathname||'')+location.search);
    const pop=()=>{const target=(location.pathname||'')+location.search;history.replaceState({},'',committedUrl.current);void navigation.current.run(target,checkLeave,url=>acceptRoute(url,true)).catch(e=>setError(errorMessage(e)));};
    if(new URLSearchParams(location.search).get('page')==='workflows')history.replaceState({},'','?page=agents&legacy_workflows=1');read();void load();window.addEventListener('popstate',pop);window.addEventListener('zhangpu:navigation',read);
    return()=>{navigation.current.cancel();window.removeEventListener('popstate',pop);window.removeEventListener('zhangpu:navigation',read);};
  }, []);
  async function navigate(p:string){if(permissionsChecking)return;const target=p.startsWith('?page=')?p:`?page=${p==='workflows'?'agents':p}`;try{await navigation.current.run(target,checkLeave,url=>acceptRoute(url));}catch(e){setError(errorMessage(e));}}
  const allowed=canOpenPage(session.user,page);const fullCanvas=canvas&&allowed&&!accessLost;
  const denialReference=accessDiagnostic&&<details><summary>查看错误代码与请求编号</summary><p><small>HTTP 状态：{accessDiagnostic.status}{accessDiagnostic.code&&<><br/>错误代码：{accessDiagnostic.code}</>}{accessDiagnostic.request_id&&<><br/>请求编号：{accessDiagnostic.request_id}</>}</small></p></details>;
  return <div className={fullCanvas?'shell canvas-shell':'shell'}>
    <aside className="sidebar"><a className="brand" href="/?page=workbench"><span className="brand-mark">Y</span><span>Y.ai</span></a><div className="workspace"><span className="workspace-icon">Y</span><div>{session.workspace?.name || '我的工作空间'}</div></div><div className="nav-label">WORKSPACE / 工作空间</div><WorkspaceNavigation items={pages.filter(p=>p.id!=='asset-api'&&p.id!=='applications'&&!permissionsChecking&&canOpenPage(session.user,p.id)&&(p.id!=='applications'||cap?.application_api?.enabled===true))} selected={page} onNavigate={id=>void navigate(id)}/><div className="sidebar-bottom"><SafetyCertificateOutlined /><p>{session.user.display_name || session.user.username}</p><Button size="small" type="text" icon={<LogoutOutlined />} onClick={() => void logout()}>退出登录</Button>{!permissionsChecking&&canOpenPage(session.user,"finance-sample")&&<button className="sample-link" onClick={() => navigate('finance-sample')}>场景样例 · 财务分析</button>}</div></aside>
    <div className="main"><header className="topbar"><div>Y.ai <span className="top-slash">/</span> <strong>{pages.find(p => p.id === page)?.title || '财务分析样例'}</strong></div><div className="top-right"><Badge status={checking ? 'processing' : error ? 'error' : 'success'} text={checking ? '检查服务中' : error ? '服务连接失败' : '服务已连接'} /><Button aria-label="刷新服务状态" type="text" icon={<ReloadOutlined />} onClick={() => void load()} />{!permissionsChecking&&canOpenPage(session.user,"accounts")&&<Button className="account-button" type="text" onClick={() => navigate('accounts')}><UserOutlined />{session.user.display_name || session.user.username}</Button>}<Button className="mobile-logout" aria-label="退出登录" type="text" icon={<LogoutOutlined />} onClick={() => void logout()} /></div></header>
    <main className="content" tabIndex={0} aria-label={`${pages.find(p=>p.id===page)?.title||'工作空间'}内容`}>{checking ? <section className="panel bootstrap-state" role="status"><span className="loading-orbit"/><h1>正在准备工作空间</h1><p>加载能力与当前账号的资源…</p></section> : error ? <Alert type="error" showIcon message="服务连接未完成" description={error} action={<Button onClick={() => void load()}>重试</Button>} /> : <>
      {permissionsChecking&&<section className="panel" role="status">正在重新确认访问权限…</section>}<div hidden={permissionsChecking} style={{display:permissionsChecking?'none':'contents'}}>
      {!allowed ? <section className="panel" role="alert"><h1>当前账号无法进入此页面</h1><p>请联系管理员调整界面权限，或从左侧选择已授权的功能。</p>{denialReference}</section> : accessLost ? <section className="panel" role="alert"><h1>当前内容已无法访问</h1><p>访问范围已变化或内容已移除，先前加载的资料已清空。</p>{denialReference}<Button onClick={()=>void navigate(page==='data'?`?page=data&section=${new URLSearchParams(location.search).get('section')==='databases'?'databases':'knowledge'}`:`?page=${page}`)}>返回列表重新加载</Button></section> : <>
      <Fragment key={dataEpoch}><div key={page} className={fullCanvas?motion.canvasFrame:motion.pageFrame}><App key={page} component={false}><PageErrorBoundary>{page === 'workbench' && <HomeWorkspace onNavigate={navigate}/>}{page === 'finance-sample' && <Workbench onNavigate={navigate} />}{page === 'data' && <DataHub />}{page === 'plugins' && <PluginCenter />}{page === 'agents' && <Agents onNavigate={navigate} modelConfigured={!!cap?.model_configured} />}{page === 'runs' && <Runs onNavigate={navigate} />}{page === 'approvals' && <ApprovalInbox/>}{page === 'accounts' && <Accounts />}{page === 'roles' && <Roles />}{page==='chunker-settings'&&<BuiltinAgents onNavigate={navigate}/>}{page==='platform-access'&&<PlatformAccess/>}{page==='asset-api'&&<AssetApiAccess onNavigate={navigate}/>}{page === 'models' && <ModelManagement />}{page === 'applications' && (cap?.application_api?.enabled===true?<ApplicationApiPage />:<Alert type="info" message="API接入尚未启用"/>)}</PageErrorBoundary></App></div></Fragment></>}</div>
    </>}</main><footer className="footer">Y.ai · 智能体工作空间</footer></div>
  </div>;
}


