'use client';
import {useEffect,useState} from 'react';
import {App,Checkbox,Form,Modal,Select} from 'antd';
import {Json,request,send} from '@/lib/api';
import {formatError,type FormattedError} from '@/lib/errors';
import {useSession} from './auth-gate';
import {ErrorBanner} from './shared';
import {accessOptions,ResourceRules} from './platform-access';
export default function RoleResourcePolicy({role,onClose,workspaceId}:{role?:Json;onClose:()=>void;workspaceId?:string}){
 const {session}=useSession();const base=workspaceId?`/platform/spaces/${workspaceId}/roles`:'/roles';const {modal}=App.useApp();const [form]=Form.useForm();const [busy,setBusy]=useState(false),[ready,setReady]=useState(false),[revision,setRevision]=useState(0),[error,setError]=useState<string|FormattedError>('');
 useEffect(()=>{let alive=true;setReady(false);setError('');if(role)void request(`${base}/${role.id}/resource-policy`).then(value=>{if(alive){form.setFieldsValue(value);setRevision(value.revision);setReady(true);}}).catch(e=>{if(alive)setError(formatError(e));});return()=>{alive=false;form.resetFields();};},[role?.id,workspaceId]);
 return <Modal open={!!role} title={`${role?.name||''} · 资源范围`} width={920} maskClosable={false} confirmLoading={busy} okButtonProps={{disabled:!ready}} onCancel={onClose} onOk={()=>modal.confirm({title:'保存后立即影响此角色的资源访问与执行。',onOk:async()=>{setBusy(true);setError('');try{const value=await form.validateFields();await send(`${base}/${role!.id}/resource-policy`,{...value,expected_revision:revision},'PUT');window.dispatchEvent(new Event('zhangpu:data-policy-changed'));onClose();}catch(e){setError(formatError(e));}finally{setBusy(false);}}})}><ErrorBanner error={error}/><Form form={form} layout="vertical"><div className="field-grid"><Form.Item name="owned_access" label="自己拥有的资源"><Select options={accessOptions}/></Form.Item><Form.Item name="owned_execute" valuePropName="checked" label="自己资源的执行"><Checkbox>允许执行</Checkbox></Form.Item></div><div className="field-grid"><Form.Item name="workspace_access" label="当前空间中的资源"><Select options={accessOptions}/></Form.Item><Form.Item name="workspace_execute" valuePropName="checked" label="空间资源的执行"><Checkbox>允许执行</Checkbox></Form.Item></div><p>执行还需角色的「资源执行」功能权限；目录可见仅包含名称、ID和状态。</p><ResourceRules name={['resources']} workspaceId={workspaceId||session.user.workspace_id}/></Form></Modal>;
}
