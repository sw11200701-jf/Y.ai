'use client';
import { ReactNode } from 'react';
import { Alert, Button, Empty, Input, Pagination, Select, Space, Table, Tabs, Tag } from 'antd';
import { ApplicationApiAppView, ApplicationApiCallFilter, ApplicationApiCallView, ApplicationApiClientView, ApplicationApiKeyView, applicationPublicUrl } from '@/lib/application-api-presentation';
import { applicationLastUsedLabel } from '@/lib/application-api-observability';
import styles from './application-api-view.module.css';

export type ApplicationApiViewProps = {
  applications: ApplicationApiAppView[]; selectedId?: string; clients: ApplicationApiClientView[]; keys: ApplicationApiKeyView[];
  calls: ApplicationApiCallView[]; callsTotal: number; callFilter: ApplicationApiCallFilter;
  busy: boolean; error?: string; canCreate: boolean; canReadRecords: boolean;
  documentation?: ReactNode;
  onSelect: (id: string) => void; onRefresh: () => void; onCreate: () => void;
  onPublish: (app: ApplicationApiAppView) => void; onConfigure: (app: ApplicationApiAppView) => void;
  onActivateVersion?: (versionId:string) => void;
  onCreateClient: () => void; onCreateKey: (client: ApplicationApiClientView) => void;
  onRevokeKey: (key: ApplicationApiKeyView) => void; onRotateKey: (key: ApplicationApiKeyView) => void;
  onFilterCalls: (filter: ApplicationApiCallFilter) => void;
};
/** Stateless display boundary: no transport, credentials, grant derivation or hidden execution. */
export default function ApplicationApiView(props: ApplicationApiViewProps) {
  const app = props.applications.find(item => item.id === props.selectedId);
  const publicUrl = applicationPublicUrl(app?.publicUrl,app?.publicUrlStatus);
  return <main className={styles.page} aria-busy={props.busy}>
    <header className={`${styles.surface} ${styles.header}`}><div><h1>API接入</h1><p>把已发布的智能体与工作流，接入你的其他平台。</p></div><Space wrap><Button disabled={props.busy} onClick={props.onRefresh}>刷新</Button>{props.canCreate && <Button type="primary" disabled={props.busy} onClick={props.onCreate}>新建应用</Button>}</Space></header>
    {props.error && <Alert type="error" message={props.error} />}
    <section className={styles.surface}>
      <label className={styles.field}>选择应用<Select className={styles.selector} aria-label="选择 API 应用" value={app?.id} disabled={props.busy} placeholder="选择一个已创建的应用" options={props.applications.map(item => ({ value: item.id, label: `${item.name} · ${item.kind === 'agent' ? '智能体' : '工作流'}` }))} onChange={props.onSelect} /></label>
      {!app ? <Empty description="创建应用后，明确选择源与发布版本，再为调用方配置 Key。" /> : <Tabs items={[
        { key: 'publications', label: '发布应用', children: <div className={styles.stack}>
          <div className={styles.header}><Space wrap><h2>{app.name}</h2><Tag>{app.statusLabel}</Tag></Space><Space wrap>{app.manage.allowed && <Button disabled={props.busy} onClick={() => props.onConfigure(app)}>应用设置</Button>}{app.publish.allowed && <Button type="primary" disabled={props.busy} onClick={() => props.onPublish(app)}>发布到 API</Button>}</Space></div>
          {!app.publish.allowed && app.publish.reason && <Alert type="info" message={app.publish.reason} />}
          <div className={styles.summary}><section><h3>新调用方默认版本</h3><p>{app.versions.find(version => version.id === app.activeVersionId)?.number || '尚未设置'}</p></section><section><h3>{app.publicUrlStatus==='development'?'本机开发 API 地址':'对外 API 地址'}</h3><p>{publicUrl ? <a href={publicUrl} rel="noreferrer" target="_blank">{publicUrl}</a> : '调用地址待配置或当前账号无权读取配置'}</p></section></div>
          <p className={styles.muted}>每次发布冻结源、参数和依赖。已有 Key 固定其允许的版本；修改默认版本不会自动升级旧调用方。</p>
          <div className={styles.table} role="region" aria-label="应用发布版本表格，可左右滚动"><Table tableLayout="fixed" scroll={{x:1100}} rowKey="id" pagination={false} dataSource={app.versions} locale={{ emptyText: '还没有发布版本。点击“发布到 API”先检查源与依赖。' }} columns={[
            { title: '发布版本', dataIndex: 'number' }, { title: '源版本', dataIndex: 'sourceLabel' }, { title: '状态', dataIndex: 'statusLabel' }, { title: '发布时间', dataIndex: 'publishedAt', render: value => value || '—' },
            { title: '固定依赖', render: (_, version) => version.dependencies.length ? <ul>{version.dependencies.map((dependency, index) => <li key={index}>{dependency.name} · {dependency.statusLabel}</li>)}</ul> : version.dependencySummaryAvailable?'无固定依赖':'此响应未提供依赖摘要' },
            {title:'默认版本',render:(_,version)=>version.id===app.activeVersionId?'新调用方默认':version.activate?.allowed&&props.onActivateVersion?<Button disabled={props.busy} onClick={()=>props.onActivateVersion?.(version.id)}>设为新调用方默认</Button>:null},
          ]} /></div>
        </div> },
        { key: 'keys', label: 'API Key', children: <div className={styles.stack}>
          <div className={styles.header}><div><h2>调用方与 Key</h2><p className={styles.muted}>同一调用方可以有多把 Key。此处只展示前缀与状态，完整 Key 仅签发当次可见。</p></div>{app.manageKeys.allowed && <Button disabled={props.busy} onClick={props.onCreateClient}>添加调用方</Button>}</div>
          {!app.manageKeys.allowed && app.manageKeys.reason && <Alert type="info" message={app.manageKeys.reason} />}
          <div className={styles.table} role="region" aria-label="调用方表格，可左右滚动"><Table tableLayout="fixed" scroll={{x:1080}} rowKey="id" pagination={false} dataSource={props.clients} locale={{ emptyText: '还没有调用方。先配置用途、固定版本与资源范围。' }} columns={[
            { title: '调用方', dataIndex: 'name' }, { title: '固定默认版本', dataIndex: 'versionLabel' }, { title: '状态', dataIndex: 'statusLabel' }, { title: '外部用户分区', render: (_, client) => client.externalUserMode === 'required' ? '每次必须指定外部用户' : '默认调用方分区' },
            { title: '允许范围', render: (_, client) => <><p>{client.scopeLabels.join('、') || '无功能'}</p><p>{client.resourceSummary.join('；') || '无资源授权'}</p></> },
            { title: '操作', render: (_, client) => app.manageKeys.allowed && <Button disabled={props.busy||client.issueKey?.allowed===false} onClick={() => props.onCreateKey(client)}>创建 Key</Button> },
          ]} /></div>
          <div className={styles.table} role="region" aria-label="API Key表格，可左右滚动"><Table tableLayout="fixed" scroll={{x:1370}} rowKey="id" pagination={false} dataSource={props.keys} locale={{ emptyText: '还没有 Key。创建后请立即保存到调用方服务器。' }} columns={[
            { title: '名称与用途', dataIndex: 'name' }, { title: 'Key 前缀', dataIndex: 'prefix' }, { title: '状态', dataIndex: 'statusLabel' }, { title: '版本', render: (_, key) => key.versionLabels.join('、') }, { title: '功能范围', render: (_, key) => key.scopeLabels.join('、') },
            { title: '最近受理新调用', render: (_, key) => applicationLastUsedLabel(key.lastUsedAt) }, { title: '到期时间', render: (_, key) => key.expiresAt || '未额外设置' },
            { title: '操作', render: (_, key) => <Space wrap>{key.rotate.allowed && <Button disabled={props.busy} onClick={() => props.onRotateKey(key)}>轮换</Button>}{key.revoke.allowed && <Button danger disabled={props.busy} onClick={() => props.onRevokeKey(key)}>撤销</Button>}</Space> },
          ]} /></div>
          <Alert type="info" message="外部用户标识仅用于分区" description="它不会变成内部账号，也不会赋予管理员或额外数据权限。应用调用 Key 与模型供应商 Key 分开管理。" />
        </div> },
        { key: 'documentation', label: '调用文档', children: props.documentation || <Empty description="请先选择发布版本。调用文档将依据该版本的参数、结果与已开放能力生成。" /> },
        { key: 'calls', label: '调用记录', children: !props.canReadRecords ? <Empty description="当前没有读取调用记录的权限。" /> : <div className={styles.stack}>
          <p className={styles.muted}>记录只含请求编号、版本、Key 前缀、时间、状态与用量，不展示客户正文、文件内容或密钥。表格可左右滚动查看全部列。</p>
          <div className={styles.filters}><label className={styles.field}>调用方<Select aria-label="筛选调用方" style={{minWidth:180}} value={props.callFilter.clientId} options={[{value:'',label:'全部调用方'},...props.clients.map(client=>({value:client.id,label:client.name}))]} onChange={clientId => props.onFilterCalls({ ...props.callFilter, clientId, page: 1 })} /></label><label className={styles.field}>状态<Select aria-label="筛选调用状态" style={{minWidth:160}} value={props.callFilter.status} options={[{value:'',label:'全部状态'},...['queued','running','resuming','paused','waiting_approval','outcome_unknown','succeeded','failed','cancelled','interrupted'].map(value=>({value,label:({reserved:'已预留',queued:'排队中',running:'运行中',resuming:'恢复中',paused:'已暂停',waiting_approval:'待审批',outcome_unknown:'结果未确认',succeeded:'成功',failed:'失败',cancelled:'已取消',interrupted:'已中断'} as Record<string,string>)[value]}))]} onChange={status => props.onFilterCalls({ ...props.callFilter, status, page: 1 })} /></label></div>
          <div className={styles.table} role="region" aria-label="调用记录表格，可左右滚动"><Table tableLayout="fixed" scroll={{x:1300}} rowKey="id" pagination={false} dataSource={props.calls} locale={{ emptyText: '暂无符合筛选条件的调用记录。' }} columns={[
            { title: '请求编号', dataIndex: 'requestId', width: 260 }, { title: '应用版本', dataIndex: 'versionLabel', width: 112 }, { title: 'Key 前缀', dataIndex: 'keyPrefix', width: 156 }, { title: '时间', dataIndex: 'createdAt', width: 180 }, { title: '总耗时（含等待）', dataIndex: 'durationLabel', width: 152 }, { title: '状态', dataIndex: 'statusLabel', width: 120 }, { title: '用量', dataIndex: 'usageLabel', width: 320 },
          ]} /></div><Pagination current={props.callFilter.page} pageSize={props.callFilter.pageSize} total={props.callsTotal} showSizeChanger={false} onChange={page => props.onFilterCalls({ ...props.callFilter, page })} />
        </div> },
      ]} />}
    </section>
  </main>;
}
