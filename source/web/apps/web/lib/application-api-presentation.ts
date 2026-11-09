import type { ApplicationResourceConstraints } from './application-api-client';
/** UI view models only: REST DTOs are projected by the management adapter. */
export type ApplicationAssetSelection = { kind: 'agent' | 'workflow'; id: string };
export type ApplicationApiCapability = { allowed: boolean; reason?: string };
export type ApplicationApiVersionView = {
  id: string; number: string; sourceLabel: string; sourceHash: string;
  statusLabel: string; publishedAt?: string; dependencies: { name: string; statusLabel: string }[];
  activate?: ApplicationApiCapability;
  dependencySummaryAvailable?: boolean;
};
export type ApplicationApiAppView = {
  id: string; name: string; kind: 'agent' | 'workflow'; statusLabel: string; activeVersionId?: string;
  publicUrl?: string; versions: ApplicationApiVersionView[];
  publicUrlStatus?: 'configured'|'development'|'not_configured';
  publish: ApplicationApiCapability; manage: ApplicationApiCapability; manageKeys: ApplicationApiCapability;
};
export type ApplicationApiClientView = {
  id: string; name: string; versionLabel: string; statusLabel: string;
  externalUserMode: 'client' | 'required'; scopeLabels: string[]; resourceSummary: string[];
  issueKey?: ApplicationApiCapability;
};
export type ApplicationApiKeyView = {
  id: string; clientId: string; name: string; prefix: string; statusLabel: string;
  lastUsedAt?: string | null; expiresAt?: string | null; scopeLabels: string[]; versionLabels: string[];
  revoke: ApplicationApiCapability; rotate: ApplicationApiCapability;
};
export type ApplicationApiCallView = {
  id: string; requestId: string; versionLabel: string; keyPrefix: string; createdAt: string;
  durationLabel: string; statusLabel: string; usageLabel: string;
};
export type ApplicationApiCallFilter = { clientId: string; status: string; page: number; pageSize: number };
export type ApplicationApiScopeOption = { value: string; label: string; description?: string };
export type ApplicationApiKeyFormValue = {
  name: string; expiresAt: string | null; scopes: string[];
  quotas: { requestsPerMinute: number; concurrency: number; dailyCalls: number; tokenBudget?: number };
  resourceConstraints?: ApplicationResourceConstraints;
};
export type ApplicationApiKeyPolicyView = {
  /** Only options/ceilings provided by the server, already intersected with Client grants. */
  scopes: ApplicationApiScopeOption[]; defaultScopes: string[]; defaultExpiresAt: string | null;
  defaults: ApplicationApiKeyFormValue['quotas']; maximum: ApplicationApiKeyFormValue['quotas'];
  resourceSummary: string[];
  resourceConstraints?: ApplicationResourceConstraints;
  expiresAtCeiling?: string | null;
  requiredResources?: ApplicationResourceConstraints['resources'];
  initialResourceConstraints?: ApplicationResourceConstraints;
};

/** Reject credentials/queries/fragments and insecure remote addresses in public links. */
export function applicationPublicUrl(value: string | undefined, status:'configured'|'development'|'not_configured'='configured'): string | undefined {
  if (!value) return;
  try {
    const url = new URL(value);
    const secure=status==='configured'&&url.protocol==='https:';
    const development=status==='development'&&url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname);
    if ((!secure&&!development) || url.username || url.password || url.search || url.hash) return;
    return url.href;
  } catch { return; }
}

export function validateApplicationKeyForm(value: ApplicationApiKeyFormValue, policy: ApplicationApiKeyPolicyView, now = Date.now()): string | undefined {
  if (!value.name.trim() || value.name.length > 100) return '请填写 1–100 个字符的用途名称。';
  if (value.expiresAt && (!Number.isFinite(Date.parse(value.expiresAt)) || Date.parse(value.expiresAt) <= now)) return '请选择将来的到期时间。';
  if (value.expiresAt && policy.expiresAtCeiling && Date.parse(value.expiresAt) > Date.parse(policy.expiresAtCeiling)) return 'Key 到期时间不能晚于调用方的到期时间。';
  if (!value.scopes.length || value.scopes.some(scope => !policy.scopes.some(option => option.value === scope))) return '请选择当前调用方允许的功能范围。';
  for (const key of ['requestsPerMinute', 'concurrency', 'dailyCalls', 'tokenBudget'] as const) {
    const amount = value.quotas[key], maximum = policy.maximum[key];
    if (key === 'tokenBudget' && amount === undefined && maximum === undefined) continue;
    if (typeof amount !== 'number' || !Number.isSafeInteger(amount) || amount < 1 || typeof maximum !== 'number' || amount > maximum) return '限额需为正整数，且不能超过调用方允许的上限。';
  }
  if (value.resourceConstraints) {
    const parent = policy.resourceConstraints;
    if (!parent || value.resourceConstraints.allowed_entities.some(entity => !parent.allowed_entities.includes(entity)) || value.resourceConstraints.resources.some(resource => !parent.resources.some(allowed => allowed.kind === resource.kind && allowed.resource_id === resource.resource_id && (allowed.version_id || null) === (resource.version_id || null) && resource.actions.length > 0 && resource.actions.every(action => allowed.actions.includes(action))))) return '数据与资源范围不能超出调用方的授权。';
  }
  if (policy.requiredResources?.some(required=>!value.resourceConstraints?.resources.some(selected=>selected.kind===required.kind&&selected.resource_id===required.resource_id&&(selected.version_id||null)===(required.version_id||null)&&required.actions.every(action=>selected.actions.includes(action))))) return '请保留当前发布版本必需的资源使用范围。';
}
