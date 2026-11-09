import type { ApplicationAssetSelection } from './application-api-presentation';

const savedId = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value) && !/^zpa_/i.test(value);
/** Location hint only. The destination must re-read the source and its current ACL. */
export function applicationApiLocation(source?: ApplicationAssetSelection): string {
  const query = new URLSearchParams({ page: 'applications' });
  if (source && (source.kind === 'agent' || source.kind === 'workflow') && savedId(source.id)) {
    query.set('source_kind', source.kind); query.set('source_id', source.id);
  }
  return `?${query}`;
}
export function readApplicationApiSource(search: string): ApplicationAssetSelection | undefined {
  const query = new URLSearchParams(search), kind = query.get('source_kind'), id = query.get('source_id');
  if ((kind === 'agent' || kind === 'workflow') && savedId(id)) return { kind, id };
}
