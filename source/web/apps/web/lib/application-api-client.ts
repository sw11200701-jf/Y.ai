import { request } from './api';

/** Paired with services/api/app/external_api/schemas.py, v1.0 management plane only. */
export type ApplicationScope = 'app.read'|'workflow.invoke'|'chat.invoke'|'runs.read'|'events.read'|'runs.cancel'|'runs.resume'|'files.upload'|'files.read'|'trace.read'|'conversations.read';
export type ApplicationQuotas = { requests_per_minute:number; concurrent_runs:number; daily_invocations:number; daily_tokens:number };
export type ApplicationResourceConstraints = { resources:{kind:string;resource_id:string;version_id?:string|null;actions:('use'|'read'|'download_original'|'export')[]}[]; allowed_entities:string[] };
export type ApplicationRuntimePolicy = { timeout_seconds:number; token_reservation:number; expose_trace:boolean; allow_resume:boolean; max_file_bytes:number; allowed_file_types:('text'|'image'|'audio'|'pdf'|'docx'|'xlsx'|'csv')[]; max_streams:number; stream_lease_seconds:number };
export type ApplicationSource = {kind:'agent'|'workflow';id:string;version_id?:string|null;expected_sha256?:string|null};
export type ApplicationDto = { application_id:string;name:string;type:'agent'|'workflow';description:string;status:'active'|'disabled'|'deleted';revision:number;active_version_id:string|null;created_at:string;updated_at:string;permissions:string[] };
export type ApplicationVersionDto = {version_id:string;application_id:string;version:number;status:'published'|'disabled';source:ApplicationSource;source_sha256:string;bundle_sha256:string;input_schema:Record<string,unknown>;output_schema:Record<string,unknown>;runtime_policy:ApplicationRuntimePolicy;created_at:string};
export type ApplicationClientDto = {client_id:string;application_id:string;name:string;status:'active'|'disabled';revision:number;default_version_id:string;scopes:ApplicationScope[];external_user_mode:'client'|'required';expires_at:string|null;quotas:ApplicationQuotas;resource_constraints:ApplicationResourceConstraints;created_at:string};
export type ApplicationKeyDto = {key_id:string;name:string;prefix:string;last4:string;status:'active'|'revoked'|'expired';revision:number;scopes:ApplicationScope[];version_ids:string[];default_version_id:string;expires_at:string|null;quotas:ApplicationQuotas;resource_constraints:ApplicationResourceConstraints;created_at:string;last_used_at:string|null;last_used_semantics:'last_new_invocation_admitted'};
export type ApplicationKeyReceiptDto = {key:ApplicationKeyDto;api_key:string;old_key_valid_until:string|null};
export type ApplicationClientOptionsDto={application_id:string;version_id:string;application_revision:number;scopes:ApplicationScope[];default_scopes:ApplicationScope[];quotas:ApplicationQuotas;external_user_modes:('client'|'required')[];resource_options:{label:string;constraint:ApplicationResourceConstraints['resources'][number];required:boolean}[];allowed_entities:string[];resource_constraints:ApplicationResourceConstraints;blockers:{code:string;message:string}[];can_create_client:boolean;public_base_url:string|null;public_base_url_status:'configured'|'development'|'not_configured'};
export type ApplicationCreateBody = {name:string;type:'agent'|'workflow';description?:string};
export type ApplicationClientBody = {name:string;default_version_id:string;scopes:ApplicationScope[];external_user_mode:'client'|'required';expires_at?:string|null;quotas:ApplicationQuotas;resource_constraints:ApplicationResourceConstraints};
export type ApplicationKeyBody = {name:string;version_ids?:string[];default_version_id?:string;scopes?:ApplicationScope[];expires_at?:string|null;quotas?:ApplicationQuotas;resource_constraints?:ApplicationResourceConstraints};
export type ApplicationPreviewBody = {source:ApplicationSource;runtime_policy:ApplicationRuntimePolicy};
export type ApplicationPreviewDto = {valid:boolean;source:ApplicationSource;source_sha256:string;bundle_sha256:string;input_schema:Record<string,unknown>;output_schema:Record<string,unknown>;runtime_policy:ApplicationRuntimePolicy;dependencies:{kind:string;id?:string;version_id?:string;actions?:string[]}[];blockers:string[]};
export type ApplicationRunStatus='queued'|'running'|'resuming'|'paused'|'waiting_approval'|'outcome_unknown'|'succeeded'|'failed'|'cancelled'|'interrupted';
export type ApplicationUsageDto={status:'known'|'partial'|'unknown'|'reserved';tokens:{prompt_tokens?:number|null;completion_tokens?:number|null;total_tokens?:number|null};reserved_tokens:number|null;source:'provider_reported'|'no_model_calls'|'persisted_ledger'|'unavailable'|'pending'};
export type ApplicationTimingDto={status:'pending'|'known'|'unknown';started_at:string|null;finished_at:string|null;elapsed_ms:number|null;includes_queue:true;basis:'admission_to_terminal'};
export type ApplicationCallDto={request_id:string;run_id:string;application_id:string;client_id:string;key_id:string;key_prefix:string;version_id:string;status:ApplicationRunStatus;safe_code:string|null;usage_status:ApplicationUsageDto['status'];usage:ApplicationUsageDto;timing:ApplicationTimingDto;created_at:string;updated_at:string};
export type ApplicationPage<T> = {items:T[];total:number;page:number;page_size:number};
type Page<T> = ApplicationPage<T>;
const id=(value:string)=>{if(!/^[A-Za-z0-9_-]{1,100}$/.test(value))throw Error('资源标识无效，请重新选择。');return encodeURIComponent(value);};
const app=(value:string)=>`/applications/${id(value)}`;
const client=(applicationId:string,clientId:string)=>`${app(applicationId)}/clients/${id(clientId)}`;
const key=(applicationId:string,clientId:string,keyId:string)=>`${client(applicationId,clientId)}/keys/${id(keyId)}`;
const get=<T>(path:string,signal?:AbortSignal)=>request<T>(path,{signal});
const write=<T>(path:string,body:unknown,method='POST',signal?:AbortSignal)=>request<T>(path,{method,body:JSON.stringify(body),signal});
const pagination=(page:number,pageSize:number)=>{if(!Number.isSafeInteger(page)||page<1||!Number.isSafeInteger(pageSize)||pageSize<1||pageSize>100)throw Error('分页参数无效。');return new URLSearchParams({page:String(page),page_size:String(pageSize)});};

/** Never accepts an application Key. All calls use the shared human session+CSRF request path. */
export const applicationApiClient={
  applications:(signal?:AbortSignal,page=1,pageSize=100)=>get<Page<ApplicationDto>>(`/applications?${pagination(page,pageSize)}`,signal),
  application:(applicationId:string,signal?:AbortSignal)=>get<ApplicationDto>(app(applicationId),signal),
  create:(body:ApplicationCreateBody,signal?:AbortSignal)=>write<ApplicationDto>('/applications',body,'POST',signal),
  patch:(applicationId:string,body:{expected_revision:number;name?:string;description?:string;status?:'active'|'disabled'|'deleted'},signal?:AbortSignal)=>write<ApplicationDto>(app(applicationId),body,'PATCH',signal),
  versions:(applicationId:string,signal?:AbortSignal,page=1,pageSize=100)=>get<Page<ApplicationVersionDto>>(`${app(applicationId)}/versions?${pagination(page,pageSize)}`,signal),
  preview:(applicationId:string,body:ApplicationPreviewBody,signal?:AbortSignal)=>write<ApplicationPreviewDto>(`${app(applicationId)}/versions/preview`,body,'POST',signal),
  publish:(applicationId:string,body:ApplicationPreviewBody&{expected_application_revision:number;expected_source_sha256:string},signal?:AbortSignal)=>write<ApplicationVersionDto>(`${app(applicationId)}/versions`,body,'POST',signal),
  activate:(applicationId:string,body:{version_id:string;expected_revision:number},signal?:AbortSignal)=>write<ApplicationDto>(`${app(applicationId)}/activate-version`,body,'POST',signal),
  clients:(applicationId:string,signal?:AbortSignal,page=1,pageSize=100)=>get<Page<ApplicationClientDto>>(`${app(applicationId)}/clients?${pagination(page,pageSize)}`,signal),
  clientOptions:(applicationId:string,versionId:string,signal?:AbortSignal)=>get<ApplicationClientOptionsDto>(`${app(applicationId)}/client-options?${new URLSearchParams({version_id:id(versionId)})}`,signal),
  contract:(applicationId:string,versionId:string,clientId:string,signal?:AbortSignal)=>get<Record<string,unknown>>(`${app(applicationId)}/contract?${new URLSearchParams({version_id:id(versionId),client_id:id(clientId)})}`,signal),
  createClient:(applicationId:string,body:ApplicationClientBody,signal?:AbortSignal)=>write<ApplicationClientDto>(`${app(applicationId)}/clients`,body,'POST',signal),
  patchClient:(applicationId:string,clientId:string,body:Partial<Omit<ApplicationClientBody,'default_version_id'|'external_user_mode'>>&{expected_revision:number;status?:'active'|'disabled'},signal?:AbortSignal)=>write<ApplicationClientDto>(client(applicationId,clientId),body,'PATCH',signal),
  keys:(applicationId:string,clientId:string,signal?:AbortSignal,page=1,pageSize=100)=>get<Page<ApplicationKeyDto>>(`${client(applicationId,clientId)}/keys?${pagination(page,pageSize)}`,signal),
  createKey:(applicationId:string,clientId:string,body:ApplicationKeyBody,signal?:AbortSignal)=>write<ApplicationKeyReceiptDto>(`${client(applicationId,clientId)}/keys`,body,'POST',signal),
  revokeKey:(applicationId:string,clientId:string,keyId:string,signal?:AbortSignal)=>write<unknown>(`${key(applicationId,clientId,keyId)}/revoke`,{},'POST',signal),
  rotateKey:(applicationId:string,clientId:string,keyId:string,body:{expected_revision:number;old_key_valid_until?:string|null},signal?:AbortSignal)=>write<ApplicationKeyReceiptDto>(`${key(applicationId,clientId,keyId)}/rotate`,body,'POST',signal),
  calls:(applicationId:string,filter:{page:number;pageSize:number;status?:string;clientId?:string},signal?:AbortSignal)=>{
    const query=pagination(filter.page,filter.pageSize);if(filter.status){if(!/^[a-z_]{1,30}$/.test(filter.status))throw Error('调用状态无效。');query.set('status',filter.status);}if(filter.clientId){id(filter.clientId);query.set('client_id',filter.clientId);}return get<Page<ApplicationCallDto>>(`${app(applicationId)}/calls?${query}`,signal);
  },
};

export async function applicationAllPages<T>(read:(page:number)=>Promise<ApplicationPage<T>>):Promise<T[]> {
  const result:T[]=[];for(let page=1;page<=1000;page++){
    const value=await read(page);if(!Array.isArray(value.items)||!Number.isSafeInteger(value.total)||value.total<0)throw Error('应用列表响应格式异常。');
    result.push(...value.items);if(result.length>=value.total)return result;if(!value.items.length)throw Error('应用列表分页已变化，请刷新。');
  }throw Error('应用列表过大，请缩小查询范围。');
}
