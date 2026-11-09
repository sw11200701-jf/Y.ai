import type { ObjectValue } from './types';
/** The published snapshot, rather than a mutable current draft, decides eligibility. */
export function nestedAgentVersion(version: ObjectValue): boolean {
  const config = version.config;
  return version.status === 'published' && !!config && !config.main_workflow_id &&
    (config.agent_type || 'simple') === 'simple' && !config.skill_bindings?.length &&
    config.execution_supported !== false;
}
export function nestedAgentVersions(versions: ObjectValue[], agentId?: string): ObjectValue[] {
  return versions.filter(version => nestedAgentVersion(version) && (!agentId || version.agent_id === agentId));
}
