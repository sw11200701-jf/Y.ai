import type { ApplicationCallDto } from './application-api-client';
import type { ApplicationApiCallView } from './application-api-presentation';
import { applicationStatusLabel } from './application-api-data';
/** Display only reported counters. Missing counters are never synthesized as zero. */
export type ApplicationTokenCounts = { prompt_tokens?: number | null; completion_tokens?: number | null; total_tokens?: number | null };
export type ApplicationUsagePresentation = { status:string; tokens?:ApplicationTokenCounts|null; reservedTokens?:number|null };
const count=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
export function applicationUsageLabel(value:ApplicationUsagePresentation):string {
  const status=({actual:'实际用量',known:'实际用量',partial:'部分已知',unknown:'用量未知',reserved:'已预留（非实际用量）'} as Record<string,string>)[value.status]||'用量状态未识别';
  const fields:[keyof ApplicationTokenCounts,string][]=[['prompt_tokens','输入'],['completion_tokens','输出'],['total_tokens','合计']];
  const counters=fields.flatMap(([key,label])=>count(value.tokens?.[key])?[`${label} ${value.tokens![key]}`]:[]);
  const reserved=count(value.reservedTokens)?`预留 ${value.reservedTokens}`:undefined;
  return [status,...counters,...(reserved?[reserved]:[])].join(' · ');
}
/** The backend defines elapsed time; the UI never subtracts arbitrary timestamps. */
export function applicationElapsedLabel(elapsedMs:unknown):string {
  return count(elapsedMs)?`${elapsedMs.toLocaleString('zh-CN')} 毫秒`:'未提供';
}
export function applicationTimingLabel(value:{status:string;elapsed_ms?:number|null}|null|undefined):string{
  if(value?.status==='pending')return '进行中';
  return value?.status==='known'?applicationElapsedLabel(value.elapsed_ms):'未记录';
}
export function applicationLastUsedLabel(value:unknown):string {
  if(typeof value!=='string'||!Number.isFinite(Date.parse(value)))return '未记录';
  return new Date(value).toLocaleString('zh-CN',{hour12:false});
}
export function applicationCallView(call:ApplicationCallDto):ApplicationApiCallView{
  const code=call.safe_code&&/^[A-Z][A-Z0-9_]{0,79}$/.test(call.safe_code)?` · ${call.safe_code}`:'';
  return {id:call.run_id,requestId:call.request_id,versionLabel:call.version_id,keyPrefix:call.key_prefix,createdAt:call.created_at,
    durationLabel:applicationTimingLabel(call.timing),statusLabel:applicationStatusLabel(call.status)+code,
    usageLabel:applicationUsageLabel({status:call.usage?.status||call.usage_status,tokens:call.usage?.tokens,reservedTokens:call.usage?.reserved_tokens})};
}
