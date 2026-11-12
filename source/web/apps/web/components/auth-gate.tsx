'use client';
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input } from 'antd';
import { clearIdentityState, Json, request, send, setCsrfToken } from '@/lib/api';
import { newPasswordRules, PASSWORD_MIN_MESSAGE } from '@/lib/password-policy';
import { errorMessage } from '@/lib/errors';
import {sessionExpiryDelay} from '@/lib/session-expiry';
type Session = { user: Json; workspace?: Json; expires_at?: string|null; persistent?:boolean };
const SessionContext = createContext<{ session: Session; logout: () => Promise<void>; refresh: () => Promise<void>; permissionsChecking:boolean } | null>(null);
export function useSession() { const value = useContext(SessionContext); if (!value) throw Error('需要有效会话'); return value; }
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'checking'|'setup'|'login'|'ready'|'error'>('checking');
  const [session, setSession] = useState<Session>(); const [epoch, setEpoch] = useState(0); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [permissionsChecking,setPermissionsChecking]=useState(false);
  const [form] = Form.useForm(); const generation = useRef(0); const channel = useRef<BroadcastChannel | null>(null);const sessionId=useRef<string|undefined>(undefined);sessionId.current=session?.user.id;
  const reset = useCallback(() => { generation.current++; clearIdentityState(); setSession(undefined);setPermissionsChecking(false); setEpoch(x => x + 1); form.resetFields(); }, [form]);
  const refresh = useCallback(async () => {
    const id = ++generation.current; setState('checking'); setError('');
    try {
      const status = await request('/auth/status'); if (id !== generation.current) return;
      if (!status.authentication_required) throw Error('当前服务未启用身份保护。请连接Yai鉴权候选 API。');
      if (!status.initialized) { setState('setup'); return; }
      try { const me = await request<Session & {csrf_token:string}>('/auth/me'); if (id !== generation.current) return; if(!me?.user||typeof me.user.id!=='string')throw Error('会话资料格式异常，请重新连接。');setCsrfToken(me.csrf_token); setSession(me); setState('ready'); }
      catch(e) { if (id !== generation.current) return; if ((e as Json).status === 401) setState('login'); else throw e; }
    } catch(e) { if (id === generation.current) { setError(errorMessage(e)); setState('error'); } }
  }, []);
  useEffect(() => {
    const expired = () => { reset(); setError('会话已结束，请重新登录。'); setState('login'); };
    const changed = () => { reset(); void refresh(); };
    // Refresh authoritative permissions without unmounting an authorized account
    // drawer. A rejected request separately removes the old page in Console.
    const revalidate = async () => {
      const id=++generation.current;setPermissionsChecking(true);
      try {
        const me=await request<Session & {csrf_token:string}>('/auth/me');
        if(id!==generation.current)return;
        if(!me?.user||typeof me.user.id!=='string'||!Array.isArray(me.user.permissions))throw Error('权限资料格式异常，请重新连接。');
        if(sessionId.current&&sessionId.current!==me.user.id)throw Error('会话身份已变化，请重新连接。');
        setSession(me);
        setCsrfToken(me.csrf_token);setState('ready');
      }catch(e){if(id!==generation.current)return;if((e as Json).status===401)expired();else{reset();setError(errorMessage(e));setState('error');}}
      finally{if(id===generation.current)setPermissionsChecking(false);}
    };
    const policyChanged=(event:Event)=>{void revalidate();if(!(event as CustomEvent).detail?.remote)channel.current?.postMessage('policy-changed');};
    const denied=()=>{void revalidate();channel.current?.postMessage('policy-changed');};
    window.addEventListener('zhangpu:session-expired', expired);
    window.addEventListener('zhangpu:permissions-changed', changed);
    window.addEventListener('zhangpu:access-denied',denied);
    window.addEventListener('zhangpu:data-policy-changed',policyChanged);
    channel.current = new BroadcastChannel('zhangpu-session'); channel.current.onmessage = event => {if(event.data==='policy-changed')window.dispatchEvent(new CustomEvent('zhangpu:data-policy-changed',{detail:{remote:true}}));else changed();};
    void refresh(); return () => { generation.current++; window.removeEventListener('zhangpu:session-expired', expired);window.removeEventListener('zhangpu:permissions-changed', changed);window.removeEventListener('zhangpu:access-denied',denied);window.removeEventListener('zhangpu:data-policy-changed',policyChanged); channel.current?.close(); };
  }, [refresh, reset]);
  useEffect(() => {
    if(state!=='ready'||!session)return;
    let timer:ReturnType<typeof setTimeout>|undefined;
    const check=()=>{const delay=sessionExpiryDelay(session);if(delay===undefined)return;if(delay===0){reset();setState('login');setError('会话已到期，请重新登录。');return;}timer=setTimeout(check,delay);};check();
    return()=>{if(timer!==undefined)clearTimeout(timer);};
  }, [state, session?.expires_at,session?.persistent, reset]);
  async function logout() { setBusy(true); let failure=''; try { await send('/auth/logout'); } catch(e) { failure='注销请求未确认，当前页面已清空。请恢复服务后重试注销。'; } finally { reset(); setError(failure); setState('login'); setBusy(false); channel.current?.postMessage('changed'); window.history.replaceState({}, '', '?page=workbench'); } }
  async function submit(values: Json) {
    setBusy(true); setError('');
    try { const route = state === 'setup' ? '/auth/setup' : '/auth/login'; const result = await send(route, { username: values.username, password: values.password, ...(state === 'setup' ? {display_name:values.display_name} : {}) }); reset(); setCsrfToken(result.csrf_token); await refresh(); channel.current?.postMessage('changed'); }
    catch(e) { setError(errorMessage(e)); form.setFieldValue('password', ''); } finally { setBusy(false); }
  }
  if (state === 'ready' && session) return <SessionContext.Provider value={{ session, logout, refresh, permissionsChecking }}><div key={`${session.user.id}:${epoch}`} aria-busy={busy}>{busy && <div className="session-blocker" role="status">正在结束会话…</div>}{session.user.must_change_password ? <RequiredPasswordChange /> : children}</div></SessionContext.Provider>;
  return <main className="auth-page"><section className="auth-panel"><a className="brand" href="/"><span className="brand-mark">Y</span><span>Yai</span></a>
    {state === 'checking' ? <div className="bootstrap-state" role="status"><span className="loading-orbit"/><h1>正在打开工作空间</h1><p>检查服务与安全会话…</p><div className="loading-lines"><i/><i/><i/></div></div> : state === 'error' ? <><h1>暂时无法打开</h1><Alert type="error" message="初始化检查未完成" description={error} /><Button type="primary" onClick={() => { reset(); void refresh(); }}>重新连接</Button></> : <>
      <div className="eyebrow">{state === 'setup' ? 'WELCOME TO Yai' : 'YOUR PRIVATE WORKSPACE'}</div><h1>{state === 'setup' ? '创建首位管理员' : '欢迎回来'}</h1><p className="subtle">{state === 'setup' ? '为这套安装设置管理员账号和密码。密码只通过本地安全表单提交。' : '登录后继续使用你的智能体、知识与工具。'}</p>{error && <Alert type="error" message={error} />}
      <Form form={form} layout="vertical" onFinish={submit} autoComplete="off"><Form.Item name="username" label="账号" rules={[{required:true,message:'请输入账号'}]}><Input autoComplete="username" disabled={busy}/></Form.Item>{state === 'setup' && <Form.Item name="display_name" label="显示名称"><Input disabled={busy}/></Form.Item>}<Form.Item name="password" label="密码" extra={state === 'setup' ? PASSWORD_MIN_MESSAGE : undefined} rules={state === 'setup' ? newPasswordRules : [{required:true,message:'请输入密码'}]}><Input.Password autoComplete={state === 'setup' ? 'new-password' : 'current-password'} disabled={busy}/></Form.Item><Button block type="primary" htmlType="submit" loading={busy}>{state === 'setup' ? '创建并进入工作空间' : '登录'}</Button></Form><Button type="link" size="small" onClick={() => { reset(); void refresh(); }}>重新检查安装状态</Button>
    </>}
  </section><p className="auth-caption">知识 · 工具 · 工作流，由你组织成章。</p></main>;
}

function RequiredPasswordChange() {
  const {refresh,logout}=useSession();const [form]=Form.useForm();const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  async function change(values:Json){setBusy(true);setError('');try{const result=await send('/auth/password',values);form.resetFields();clearIdentityState();setCsrfToken(result.csrf_token);await refresh();}catch(e){setError(errorMessage(e));form.setFieldsValue({current_password:'',new_password:'',confirmation:''});}finally{setBusy(false);}}
  return <main className="auth-page"><section className="auth-panel"><div className="eyebrow">ACCOUNT SECURITY</div><h1>设置你的新密码</h1><p className="subtle">当前使用的是初始密码。设置新密码后即可进入工作空间。</p>{error&&<Alert type="error" message={error}/>}<Form form={form} layout="vertical" onFinish={({current_password,new_password})=>void change({current_password,new_password})}>
    <Form.Item name="current_password" label="当前初始密码" rules={[{required:true,message:'请输入当前初始密码'}]}><Input.Password autoComplete="current-password" disabled={busy}/></Form.Item>
    <Form.Item name="new_password" label="新密码" extra={PASSWORD_MIN_MESSAGE} rules={newPasswordRules}><Input.Password autoComplete="new-password" disabled={busy}/></Form.Item>
    <Form.Item name="confirmation" label="再次输入新密码" dependencies={['new_password']} rules={[{required:true,message:'请再次输入新密码'},({getFieldValue})=>({validator:(_,value)=>value===getFieldValue('new_password')?Promise.resolve():Promise.reject(new Error('两次输入的密码不一致'))})]}><Input.Password autoComplete="new-password" disabled={busy}/></Form.Item>
    <Button block type="primary" htmlType="submit" loading={busy}>保存新密码并进入</Button></Form><Button type="link" disabled={busy} onClick={()=>void logout()}>退出登录</Button></section></main>;
}
