'use client';
import {Checkbox,Tag} from 'antd';
import {permissionColumnLabels,permissionPresentation,permissionSummaries,type PermissionItem} from '@/lib/permission-presentation';
import {permissionLabel} from '@/lib/ui-labels';

/** Display grouping never filters, defaults or rewrites the controlled permission array. */
export function PermissionMatrix({catalog,grantable,value=[],onChange}:{catalog:PermissionItem[];grantable:PermissionItem[];value?:string[];onChange?:(value:string[])=>void}){
 const {rows,columns,other,missing}=permissionPresentation(catalog,value),editable=new Set(grantable.map(item=>item.key));
 const toggle=(key:string,checked:boolean)=>{if(!editable.has(key))return;onChange?.(checked?value.includes(key)?[...value]:[...value,key]:value.filter(item=>item!==key));};
 const control=(item:PermissionItem,fullName=false)=>editable.has(item.key)?<Checkbox aria-label={permissionLabel(item.key,item.name)} checked={value.includes(item.key)} onChange={event=>toggle(item.key,event.target.checked)}>{fullName?permissionLabel(item.key,item.name):item.key==='runs.approve'?'审批指定运行':null}</Checkbox>:<span className="permission-unavailable" title="此项不在当前可授予范围内">{value.includes(item.key)?'已包含 · 不可配置':'不可配置'}</span>;
 const needsAccess=rows.filter(row=>row.cells.manage&&value.includes(row.cells.manage.key)&&(!row.cells.page||!value.includes(row.cells.page.key)));
 return <section className="permission-editor" aria-label="功能权限"><div className="permission-section-heading"><h3>功能权限</h3><small>页面访问与操作能力分别配置</small></div>
 {rows.length>0&&<div className="permission-matrix-scroll" tabIndex={0} role="region" aria-label="功能权限矩阵"><table className="permission-matrix"><caption>按模块配置功能权限</caption><thead><tr><th scope="col">功能模块</th>{columns.map(column=><th scope="col" key={column}>{permissionColumnLabels[column]}</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.id}><th scope="row"><span>{row.title}</span>{row.detail&&<small>{row.detail}</small>}</th>{columns.map(column=><td key={column}>{row.cells[column]?control(row.cells[column]):<span className="permission-unavailable" aria-label={`此模块未提供${permissionColumnLabels[column]}`}>—</span>}</td>)}</tr>)}</tbody></table></div>}
 {(other.length>0||missing.length>0)&&<div className="permission-other"><h4>其他权限</h4>{other.map(item=><div key={item.key}>{control(item,true)}{!editable.has(item.key)&&<span>{permissionLabel(item.key,item.name)}</span>}</div>)}{missing.map(key=><p key={key}><Tag>{key}</Tag><small>当前目录未返回，原值保留</small></p>)}</div>}
 {!rows.length&&!other.length&&!missing.length&&<p className="permission-empty">当前权限目录没有可配置项。</p>}
 {needsAccess.map(row=><p className="permission-pair-note" key={row.id}>{row.title}：已选择管理权限，{row.cells.page?'尚未选择页面访问；如需从管理页操作，请分别核对两项配置。':'当前目录未提供页面访问项，请核对权限目录配置。'}</p>)}
 <p className="permission-boundary-note">功能权限决定可访问页面与管理操作；可使用的数据范围由数据授权单独控制</p></section>;
}
export function PermissionSummary({value,catalog}:{value:string[];catalog:PermissionItem[]}){
 const summaries=permissionSummaries(value,catalog);return <div className="permission-summary">{summaries.length?summaries.map(item=><Tag key={item.id}><strong>{item.label}</strong><span>{item.details.join('、')}</span></Tag>):<span className="subtle">未配置功能权限</span>}</div>;
}
