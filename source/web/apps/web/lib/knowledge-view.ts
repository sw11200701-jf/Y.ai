import { Json } from './api';
// Public previews carry source row numbers; never infer correction coordinates
// from a rendered table's current page or from mapped target column names.
export function workingPreview(batch: Json): { row: number; values: Json; locator?: string }[] {
  return (batch.working_preview || batch.original_preview || []).map((item: Json) => ({row:item.row,values:item.values||{},locator:item.locator||item.source_locator}));
}
export function sourceColumns(batch: Json): string[] {
  return batch.columns || Object.keys(batch.original_preview?.[0]?.values || {});
}
