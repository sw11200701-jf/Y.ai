// Display names only. Permission keys and independent capabilities stay intact.
export const menuLabels = {
  workbench: '工作台', agents: 'AI工作室', data: '数据中心',
  applications:'API接入', plugins: '插件与服务', models: '模型管理', runs: '运行记录', approvals:'待我审批', accounts: '账号与会话', roles: '角色与权限',
} as const;
export const dataSectionLabels = {
  knowledge: `${menuLabels.data} · 知识库`, databases: `${menuLabels.data} · 数据库`,
} as const;
export const permissionLabels: Record<string,string> = {
  'page.applications': 'API接入 · 页面访问',
  'applications.read': 'API接入 · 读取应用',
  'applications.manage': 'API接入 · 管理应用',
  'applications.publish': 'API接入 · 发布版本',
  'applications.keys.manage': 'API接入 · 管理调用方与Key',
  'applications.records.read': 'API接入 · 读取调用记录',
  'applications.runs.control': 'API接入 · 控制应用运行',

  'page.workbench': `${menuLabels.workbench} · 页面访问`,
  'page.ai': `${menuLabels.agents} · 页面访问`,
  'page.knowledge': `${dataSectionLabels.knowledge} · 页面访问`,
  'page.database': `${dataSectionLabels.databases} · 页面访问`,
  'page.plugins': `${menuLabels.plugins} · 页面访问`,
  'page.models': `${menuLabels.models} · 页面访问`,
  'models.manage': `${menuLabels.models} · 管理`,
  'page.runs': `${menuLabels.runs} · 页面访问`,
  'page.accounts': `${menuLabels.accounts} · 页面访问`,
  'accounts.manage': `${menuLabels.accounts} · 管理`,
  'page.roles': `${menuLabels.roles} · 页面访问`,
  'roles.manage': `${menuLabels.roles} · 管理`,
  'runs.approve': `${menuLabels.runs} · 审批指定运行`,
};
export function permissionLabel(key:string,fallback=key) { return Object.hasOwn(permissionLabels,key) ? permissionLabels[key] : fallback; }
