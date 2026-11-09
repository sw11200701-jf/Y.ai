'use client';
import { Select } from 'antd';
import { Binding, Json } from '@/lib/api';
export function BindingFields({ versions, connections, binding, onChange }: { versions: Json[]; connections: Json[]; binding: Partial<Binding>; onChange: (b: Partial<Binding>) => void }) {
  const version = versions.find(v => v.id === binding.plugin_version_id);
  return <div className="toolbar"><Select aria-label="插件发布版本" placeholder="选择固定发布版本" value={binding.plugin_version_id} onChange={v => onChange({ ...binding, plugin_version_id: v, tool_id: undefined })} options={versions.map(v => ({ value: v.id, label: `${v.name} · v${v.version}` }))} /><Select aria-label="插件工具" placeholder="选择具体工具" value={binding.tool_id} onChange={v => onChange({ ...binding, tool_id: v })} options={(version?.tools || []).map((t: Json) => ({ value: t.id, label: t.name }))} /><Select aria-label="服务连接" placeholder="选择连接" value={binding.connection_id} onChange={v => onChange({ ...binding, connection_id: v })} options={connections.filter(c => c.enabled !== false).map(c => ({ value: c.id, label: c.name }))} /></div>;
}
