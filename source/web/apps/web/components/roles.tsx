'use client';
import { menuLabels } from '@/lib/ui-labels';
import { useEffect,useRef,useState } from 'react';
import { App,Button,Form,Input,Modal,Popconfirm,Select,Space,Table,Tag } from 'antd';
import { Json,list,request,send } from '@/lib/api';
import { hasPermission } from '@/lib/permissions';
import { formatError,type FormattedError } from '@/lib/errors';
import { useSession } from './auth-gate';
import { roleDirectory, permissionDirectory } from '@/lib/identity-directory';
import {PermissionMatrix,PermissionSummary} from './permission-matrix';
import type {PermissionItem} from '@/lib/permission-presentation';
import {useLeaveConfirmation} from './workflow-studio/use-leave-confirmation';
import { ErrorBanner,PageTitle } from './shared';
import SessionPolicies from './session-policies';
import RoleResourcePolicy from './role-resource-policy';
export default function Roles(){
  const {session}=useSession();const {message,modal}=App.useApp();const allowed=session.user.role==='super_admin'&&hasPermission(session.user,'roles.manage');
  const [scopeRole,setScopeRole]=useState<Json>();
  const [directoryReady,setDirectoryReady]=useState(false);const [roles,setRoles]=useState<Json[]>([]);const [catalog,setCatalog]=useState<PermissionItem[]>([]);const [editDirty,setEditDirty]=useState(false);const [editing,setEditing]=useState<Json>();const [open,setOpen]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState<string|FormattedError>('');const [form]=Form.useForm();const base=Form.useWatch('base_role',form);const dirtyRef=useRef(false),busyRef=useRef(false);dirtyRef.current=editDirty;busyRef.current=busy;
  const {guard:closeGuard}=useLeaveConfirmation({dirty:()=>dirtyRef.current,blocked:()=>busyRef.current,container:()=>document.body,show:options=>modal.confirm({...options,title:'角色修改尚未保存',okText:'放弃修改并关闭'}),onError:()=>setError('暂时无法显示关闭提示，请稍后重试。')});
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(formatError(e));}finally{setBusy(false);}}
  async function load(){setDirectoryReady(false);const [r,p]=await Promise.all([list('/roles'),list('/permissions')]);const validRoles=roleDirectory(r);const validCatalog=permissionDirectory(p) as PermissionItem[];setRoles(validRoles);setCatalog(validCatalog);setDirectoryReady(true);}
  useEffect(()=>{if(allowed)void act(load);},[allowed]);
  if(!allowed)return <ErrorBanner error="当前账号没有角色管理权限。"/>;
  const grantable=catalog.filter(p=>!['roles.manage','page.roles'].includes(p.key)&&(base!=='member'||p.key!=='accounts.manage'));
  function edit(role?:Json){setEditDirty(false);setEditing(role);form.resetFields();form.setFieldsValue(role?{name:role.name,base_role:role.base_role,permissions:role.permissions}:{name:'',base_role:'member',permissions:[]});setOpen(true);}
  async function closeEditor(){if(await closeGuard()!==true)return;setOpen(false);setEditDirty(false);form.resetFields();}
  return <><PageTitle eyebrow="ROLE ACCESS" title={menuLabels.roles} description="按功能模块配置页面访问、管理权限与专项操作。" action={<Button type="primary" disabled={!directoryReady} onClick={()=>edit()}>新建角色</Button>}/><ErrorBanner error={error}/><SessionPolicies/><RoleResourcePolicy role={scopeRole} onClose={()=>setScopeRole(undefined)}/><section className="panel"><Table<Json> rowKey="id" dataSource={roles} loading={busy} columns={[{title:'角色',render:(_,r)=><Space>{r.name}{r.builtin&&<Tag>内置</Tag>}</Space>},{title:'基础身份',render:(_,r)=>({super_admin:'超级管理员',admin:'管理员',member:'普通用户'}[r.base_role as string]||r.base_role)},{title:'功能权限',render:(_,r)=><PermissionSummary value={r.permissions} catalog={catalog}/>},{title:'操作',render:(_,r)=><Space><Button disabled={!directoryReady} onClick={()=>edit(r)}>编辑</Button><Button size="small" onClick={()=>setScopeRole(r)}>资源范围</Button>{!r.builtin&&<Popconfirm title="删除此角色？仍有账号使用时无法删除。" onConfirm={()=>act(async()=>{await request(`/roles/${r.id}`,{method:'DELETE'});await load();})}><Button danger>删除</Button></Popconfirm>}</Space>}]} /></section>
  <Modal className="permission-editor-modal" centered width="min(760px,calc(100vw - 24px))" okText="保存配置" cancelText="取消" forceRender maskClosable={false} open={open} title={editing?'编辑角色':'新建角色'} confirmLoading={busy} okButtonProps={{disabled:!directoryReady}} onCancel={closeEditor} onOk={()=>void act(async()=>{if(!directoryReady)throw Error('权限资料尚未重新读取，当前修改不能提交。');const v=await form.validateFields();await send(editing?`/roles/${editing.id}`:'/roles',editing?{...(!editing.builtin?{name:v.name}:{}),permissions:v.permissions||[]}:{...v,permissions:v.permissions||[]},editing?'PATCH':'POST');setOpen(false);setEditDirty(false);form.resetFields();await load();window.dispatchEvent(new Event('zhangpu:data-policy-changed'));modal.success({title:'角色权限已保存',content:'最新权限已重新读取，后续请求按新权限执行。',centered:true,okText:'知道了'});})}><ErrorBanner error={error}/><Form form={form} layout="vertical" onValuesChange={()=>setEditDirty(true)}><div className="field-grid"><Form.Item name="name" label="角色名称" rules={[{required:true,whitespace:true,message:'请输入角色名称'}]}><Input disabled={!!editing?.builtin}/></Form.Item><Form.Item name="base_role" label="基础身份"><Select disabled={!!editing} options={[{value:'member',label:'普通用户'},{value:'admin',label:'管理员'}]} onChange={()=>form.setFieldValue('permissions',[])}/></Form.Item></div><Form.Item name="permissions"><PermissionMatrix catalog={catalog} grantable={grantable}/></Form.Item></Form></Modal></>;
}
