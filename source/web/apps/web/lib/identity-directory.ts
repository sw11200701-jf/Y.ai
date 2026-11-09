import type { Json } from './api';
import { permissionLabel } from './ui-labels';

function invalid(field:string):never {
  throw Object.assign(new Error(`账号权限资料格式不完整，请刷新后重试（${field}）。`),{code:'UI_RESPONSE_INVALID'});
}
function strings(value:unknown,field:string):string[]{
  if(!Array.isArray(value)||value.some(v=>typeof v!=='string'||!v))invalid(field);
  return [...value];
}
export function roleDirectory(items:unknown):Json[]{
  if(!Array.isArray(items))invalid('roles');
  return items.map((role,index)=>{
    if(!role||typeof role.id!=='string'||typeof role.name!=='string'||!['super_admin','admin','member'].includes(role.base_role))invalid(`roles.${index}`);
    return {...role,permissions:strings(role.permissions,`roles.${index}.permissions`)};
  });
}
export function permissionDirectory(items:unknown):Json[]{
  if(!Array.isArray(items))invalid('permissions');
  return items.map((permission,index)=>{
    if(!permission||typeof permission.key!=='string'||typeof permission.name!=='string')invalid(`permissions.${index}`);
    return {...permission,name:permissionLabel(permission.key,permission.name)};
  });
}
export function accountDirectory(items:unknown,roles:Json[]):Json[]{
  if(!Array.isArray(items))invalid('users');
  return items.map((user,index)=>{
    if(!user||typeof user.id!=='string'||typeof user.username!=='string'||typeof user.active!=='boolean'||!['super_admin','admin','member'].includes(user.role))invalid(`users.${index}`);
    if(user.display_name!=null&&typeof user.display_name!=='string')invalid(`users.${index}.display_name`);
    const roleIds=user.role_ids===undefined&&typeof user.role_id==='string'?[user.role_id]:user.role_ids;
    const normalized={...user,role_ids:strings(roleIds,`users.${index}.role_ids`),permissions:strings(user.permissions,`users.${index}.permissions`)};
    if(!Object.hasOwn(user,'permission_overrides'))invalid(`users.${index}.permission_overrides`);
    if(!normalized.role_ids.length||new Set(normalized.role_ids).size!==normalized.role_ids.length)invalid(`users.${index}.role_ids`);
    if(user.permission_overrides!=null)normalized.permission_overrides=strings(user.permission_overrides,`users.${index}.permission_overrides`);
    return normalized;
  });
}
