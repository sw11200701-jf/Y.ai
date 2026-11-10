import type { Json } from './api';

export const pagePermissions: Record<string,string> = {
  workbench:'page.workbench', 'finance-sample':'page.workbench', agents:'page.ai',
  data:'page.knowledge', knowledge:'page.knowledge', databases:'page.database', plugins:'page.plugins', models:'page.models', runs:'page.runs', accounts:'page.accounts', roles:'page.roles',
  approvals:'runs.approve', applications:'page.applications',
};
export function hasPermission(user: Json, permission: string) {
  return Array.isArray(user.permissions) && user.permissions.includes(permission);
}
export function canOpenPage(user: Json, page: string) {
  if(page==='models')return typeof user.id==='string'&&!!user.id;
  if(page==='workspace-logs')return user.role==='super_admin';
  if(page==='platform-access')return ['platform.keys.manage','platform.keys.admin','spaces.manage'].some(key=>hasPermission(user,key));
  if(page==='asset-api')return ['page.ai','page.workbench'].some(key=>hasPermission(user,key));
  if(page==='chunker-settings')return ['chunker.config.view','chunker.config.read','chunker.config.manage'].some(key=>hasPermission(user,key));
  if(page==='data')return hasPermission(user,'page.knowledge')||hasPermission(user,'page.database');
  const permission=pagePermissions[page];
  return !!permission && hasPermission(user,permission);
}
