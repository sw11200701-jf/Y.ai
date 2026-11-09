'use client';
import {useEffect,useState} from 'react';
import {Button,Form,Input,Modal,Select,Space} from 'antd';
import {Json,list,send} from '@/lib/api';
import {formatError,type FormattedError} from '@/lib/errors';
import {useSession} from './auth-gate';
import {hasPermission} from '@/lib/permissions';
import {permissionDirectory} from '@/lib/identity-directory';
import type {PermissionItem} from '@/lib/permission-presentation';
import {PermissionMatrix} from './permission-matrix';
import {ErrorBanner} from './shared';
import RoleResourcePolicy from './role-resource-policy';

export default function SpaceRoleEditor({workspaceId,roles,onSaved}:{workspaceId:string;roles:Json[];onSaved:()=>Promise<void>}){
 const {session}=useSession();const [form]=Form.useForm();const [open,setOpen]=useState(false),[editing,setEditing]=useState<Json>(),[scope,setScope]=useState<Json>(),[catalog,setCatalog]=useState<PermissionItem[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState<string|FormattedError>('');
 useEffect(()=>{let active=true;if(hasPermission(session.user,'roles.manage'))void list('/permissions').then(values=>{if(active)setCatalog(permissionDirectory(values) as PermissionItem[]);}).catch(e=>{if(active)setError(formatError(e));});return()=>{active=false;};},[workspaceId]);
 if(!hasPermission(session.user,'roles.manage'))return null;
 function edit(role?:Json){setEditing(role);setError('');form.resetFields();form.setFieldsValue(role||{base_role:'member',permissions:[]});setOpen(true);}
 return <section style={{marginTop:20}}><h3>此空间的角色配置</h3><ErrorBanner error={error}/><Space wrap><Button onClick={()=>edit()}>新建空间角色</Button>{roles.map(role=><Space key={role.id}><Button onClick={()=>edit(role)}>{role.name} · 功能权限</Button><Button onClick={()=>setScope(role)}>资源范围</Button></Space>)}</Space><RoleResourcePolicy workspaceId={workspaceId} role={scope} onClose={()=>setScope(undefined)}/><Modal title={editing?`${editing.name} · 此空间功能权限`:'新建空间角色'} open={open} width={800} maskClosable={false} confirmLoading={busy} onCancel={()=>setOpen(false)} onOk={async()=>{setBusy(true);setError('');try{const value=await form.validateFields();await send(`/platform/spaces/${workspaceId}/roles${editing?'/'+editing.id:''}`,editing?{permissions:value.permissions,...(!editing.builtin?{name:value.name}:{}),expected_updated_at:editing.updated_at}:value,editing?'PATCH':'POST');await onSaved();setOpen(false);}catch(e){setError(formatError(e));}finally{setBusy(false);}}}><ErrorBanner error={error}/><Form form={form} layout="vertical"><Form.Item name="name" label="角色名称" rules={[{required:true,message:'请输入名称'}]}><Input disabled={editing?.builtin}/></Form.Item>{!editing&&<Form.Item name="base_role" label="基础身份"><Select options={[{value:'member',label:'普通用户'},{value:'admin',label:'管理员'}]}/></Form.Item>}<Form.Item name="permissions" label="功能权限"><PermissionMatrix catalog={catalog} grantable={catalog.filter(item=>hasPermission(session.user,item.key))}/></Form.Item></Form></Modal></section>;
}
