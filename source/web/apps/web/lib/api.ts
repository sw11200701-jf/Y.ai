import { formatError } from './errors';
import { invalidatesAccess } from './access-invalidation';
export type Json = Record<string, any>;
export type Binding = { plugin_version_id: string; tool_id: string; connection_id: string };
export type Workflow = { id?: string; name: string; version: string; mode: 'demo' | 'model'; nodes: { id: string; type: string; config: Json }[]; edges: { source: string; target: string }[] };
export const API = '/api/v1';
let csrfToken: string | undefined;
let identityEpoch = 0;
const pending = new Set<AbortController>();
const objectUrls = new Set<string>();
export function setCsrfToken(value?: string) { csrfToken = value; }
export function clearIdentityState() {
  csrfToken = undefined; clearAccessState();
}
function clearAccessState() {
  identityEpoch++;
  pending.forEach(controller => controller.abort()); pending.clear();
  objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls.clear();
}
export type AccessDenialDiagnostic = { status:403|404; code?:string; request_id?:string };
/** Only application error tokens and the app middleware's UUID hex ID may cross this event. */
export function sanitizeAccessDenial(value:unknown):AccessDenialDiagnostic|undefined {
  if(!value||typeof value!=='object'||Array.isArray(value))return;
  const data=value as Record<string,unknown>;
  if(data.status!==403&&data.status!==404)return;
  return {status:data.status,
    ...(typeof data.code==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(data.code)&&!/[\r\n]/.test(data.code)?{code:data.code}:{}),
    ...(typeof data.request_id==='string'&&data.request_id.length===32&&/^[a-fA-F0-9]{32}$/.test(data.request_id)?{request_id:data.request_id}:{})};
}
function accessDenied(path:string,status:number,code?:string,requestId?:unknown) {
  if(!invalidatesAccess(path,status,code))return;
  clearAccessState();
  const event=new Event('zhangpu:access-denied');
  Object.defineProperty(event,'detail',{value:Object.freeze(sanitizeAccessDenial({status,code,request_id:requestId}))});
  window.dispatchEvent(event);
}
export function currentIdentityEpoch() { return identityEpoch; }
async function ensureCsrf() {
  if (!csrfToken) {
    const epoch = identityEpoch;
    const response = await fetch(`${API}/auth/csrf`, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw Error('无法建立安全会话，请重试');
    const body = await response.json();
    if (epoch !== identityEpoch) throw new DOMException('身份已变化', 'AbortError');
    csrfToken = body.csrf_token;
  }
  return csrfToken!;
}
export async function request<T = Json>(path: string, init?: RequestInit): Promise<T> {
  const epoch = identityEpoch; const controller = new AbortController(); pending.add(controller);
  try {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes((init?.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', await ensureCsrf());
  if (epoch !== identityEpoch) throw new DOMException('身份已变化', 'AbortError');
  const response = await fetch(`${API}${path}`, { cache: 'no-store', credentials: 'same-origin', ...init, signal: init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal, headers });
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('json') ? await response.json() : null;
  if (epoch !== identityEpoch) throw new DOMException('身份已变化', 'AbortError');
  if (response.status === 403 && /CSRF/i.test(body?.error?.code || '')) csrfToken = undefined;
  if (response.status === 401 && !path.startsWith('/auth/')) { clearIdentityState(); window.dispatchEvent(new Event('zhangpu:session-expired')); }
  if(response.status===403&&body?.error?.code==='PASSWORD_CHANGE_REQUIRED')window.dispatchEvent(new Event('zhangpu:permissions-changed'));
  if(!response.ok)accessDenied(path,response.status,body?.error?.code,body?.request_id);
  if (!response.ok) { const details=body?.error?.details||body?.detail;const failure={message:body?.error?.message,code:body?.error?.code,details,request_id:body?.request_id};throw Object.assign(new Error(formatError(failure).message),{status:response.status,code:failure.code,details,request_id:failure.request_id}); }
  return body as T;
  } finally { pending.delete(controller); }
}
export const send = <T = Json>(path: string, body?: unknown, method = 'POST') => request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
export async function list(path: string, init?:RequestInit): Promise<Json[]> {
  const first = await request<{ items: Json[]; total?: number; page?: number; page_size?: number }>(path,init);
  const items = first.items.slice();
  if (Number.isFinite(first.total) && first.page_size && first.page) {
    const [route, query = ''] = path.split('?'); const params = new URLSearchParams(query);
    for (let page = first.page + 1; (page - 1) * first.page_size < first.total!; page++) {
      params.set('page', String(page)); params.set('page_size', String(first.page_size));
      const next = await request<{ items: Json[] }>(`${route}?${params}`,init); if (!next.items.length) break; items.push(...next.items);
    }
  }
  return items;
}
/** Authorized local original-file reads share download error and identity handling. */
export async function readDownload(path: string, fallback: string, signal?:AbortSignal) {
  const epoch = identityEpoch;
  const controller = new AbortController(); pending.add(controller);
  try {
  const response = await fetch(`${API}${path}`, { credentials: 'same-origin', cache: 'no-store', signal: signal?AbortSignal.any([signal,controller.signal]):controller.signal });
  if(epoch!==identityEpoch)throw new DOMException('身份已变化','AbortError');
  if (response.status === 401) { clearIdentityState(); window.dispatchEvent(new Event('zhangpu:session-expired')); }
  if (!response.ok) { const body = await response.json().catch(() => ({}));if(epoch!==identityEpoch)throw new DOMException('身份已变化','AbortError'); if(response.status===403&&/CSRF/i.test(body?.error?.code||''))csrfToken=undefined;if(response.status===403&&body?.error?.code==='PASSWORD_CHANGE_REQUIRED')window.dispatchEvent(new Event('zhangpu:permissions-changed'));accessDenied(path,response.status,body?.error?.code,body?.request_id);const failure={...body?.error,request_id:body?.request_id};throw Object.assign(new Error(formatError(failure).message),failure,{status:response.status}); }
  const disposition = response.headers.get('content-disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const filename = encoded ? decodeURIComponent(encoded) : disposition.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
  const blob = await response.blob(); if (epoch !== identityEpoch||signal?.aborted) throw new DOMException('身份或读取范围已变化', 'AbortError');
  return {blob,filename,identityEpoch:epoch,headers:response.headers};
  } finally { pending.delete(controller); }
}
export async function download(path:string,fallback:string,signal?:AbortSignal){
 const {blob,filename,identityEpoch:epoch}=await readDownload(path,fallback,signal);
 if(epoch!==identityEpoch||signal?.aborted)throw new DOMException('身份或读取范围已变化','AbortError');
 const url=URL.createObjectURL(blob);objectUrls.add(url);const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>{URL.revokeObjectURL(url);objectUrls.delete(url);},1000);
}
export const defaultQuery = { entity: 'SYNTHETIC-DEMO', periods: ['2024', '2025'], metric_codes: ['revenue', 'net_profit', 'operating_cash_flow', 'total_assets', 'total_liabilities'], currency: 'CNY', unit: 'yuan', scope: 'consolidated' };
export const metricNames: Record<string, string> = { revenue: '营业收入', net_profit: '净利润', operating_cash_flow: '经营现金流', total_assets: '资产总额', total_liabilities: '负债总额', revenue_yoy: '收入同比', net_profit_yoy: '净利润同比', net_margin: '净利率', net_profit_margin: '净利率', debt_ratio: '资产负债率', asset_liability_ratio: '资产负债率' };
export function decimal(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const raw = String(value); if (!/^-?\d+(\.\d+)?$/.test(raw)) return raw;
  const [whole, fraction] = raw.split('.'); return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? `.${fraction}` : '');
}
export function time(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'; }
// Decimal-point shift is display formatting only; all ratios are calculated by the API.
export function ratioText(value: unknown): string { if (value === null || value === undefined) return '—'; const s = String(value); if (!/^-?\d+(\.\d+)?$/.test(s)) return s; const negative = s.startsWith('-'); const [whole, fraction = ''] = s.replace('-', '').split('.'); const digits = whole + fraction.padEnd(2, '0'); const point = whole.length + 2; const left = digits.slice(0, point).replace(/^0+(?=\d)/, ''); const right = digits.slice(point).replace(/0+$/, ''); return `${negative ? '-' : ''}${left}${right ? '.' + right : ''}%`; }

