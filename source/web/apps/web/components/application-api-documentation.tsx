'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Space, Table, Tabs } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';
import { applicationApiDocumentation, applicationOpenApiYaml, bindApplicationOpenApi, type ApplicationApiDocumentSnapshot } from '@/lib/application-api-documentation';
import styles from './application-api-view.module.css';

export default function ApplicationApiDocumentation({ snapshot, contractKey, loadOpenApi }: { snapshot: ApplicationApiDocumentSnapshot; contractKey:string; loadOpenApi:(signal:AbortSignal)=>Promise<unknown> }) {
  const urls = useRef(new Set<string>());
  const [error, setError] = useState('');
  const [accessValid,setAccessValid]=useState(true);
  const [openApi,setOpenApi]=useState<{key:string;document:Record<string,unknown>;snapshot:ApplicationApiDocumentSnapshot}>();
  const currentBinding=openApi?.key===contractKey?openApi:undefined;
  const currentOpenApi=currentBinding?.document,effectiveSnapshot=currentBinding?.snapshot||snapshot;
  const document = useMemo(() => { try { return applicationApiDocumentation(effectiveSnapshot); } catch { return undefined; } }, [effectiveSnapshot]);
  const [loading,setLoading]=useState(false),[retry,setRetry]=useState(0);
  const generation=useRef(0),loader=useRef(loadOpenApi),currentSnapshot=useRef(snapshot);loader.current=loadOpenApi;currentSnapshot.current=snapshot;
  const activeRequest=useRef<AbortController|null>(null);
  const epoch = useRef(currentIdentityEpoch());
  const invalidated = useRef(false);
  useEffect(() => {
    invalidated.current = false; epoch.current = currentIdentityEpoch();setAccessValid(true);
    const clear = () => { invalidated.current = true; generation.current++;activeRequest.current?.abort();setAccessValid(false);setOpenApi(undefined); urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear(); };
    const events = ['zhangpu:session-expired', 'zhangpu:permissions-changed', 'zhangpu:access-denied', 'zhangpu:data-policy-changed'];
    events.forEach(event => window.addEventListener(event, clear));
    return () => { clear(); events.forEach(event => window.removeEventListener(event, clear)); };
  }, [contractKey]);
  useEffect(()=>{
    if(invalidated.current)return;
    const ticket=++generation.current,identity=currentIdentityEpoch(),controller=new AbortController();activeRequest.current=controller;setOpenApi(undefined);setError('');setLoading(true);
    const valid=()=>!controller.signal.aborted&&!invalidated.current&&ticket===generation.current&&identity===currentIdentityEpoch();
    void loader.current(controller.signal).then(value=>{if(valid())setOpenApi({key:contractKey,...bindApplicationOpenApi(value,currentSnapshot.current)});}).catch(()=>{if(valid())setError('正式接口文档暂未读取完成，请重试或核对服务端调用地址配置。');}).finally(()=>{if(valid())setLoading(false);});
    return()=>{controller.abort();if(activeRequest.current===controller)activeRequest.current=null;};
  },[contractKey,retry]);
  function download(format: 'json' | 'yaml') {
    if (!document || !currentOpenApi || invalidated.current || epoch.current !== currentIdentityEpoch()) return;
    try {
      const contents = format === 'json' ? JSON.stringify(currentOpenApi, null, 2) : applicationOpenApiYaml(currentOpenApi);
      const url = URL.createObjectURL(new Blob([contents], { type: format === 'json' ? 'application/json' : 'application/yaml' })); urls.current.add(url);
      const link = window.document.createElement('a'); link.href = url; link.download = `application-openapi.${format}`; link.click();
      // Allow the browser to start its download; policy/unmount cleanup revokes earlier.
      setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); }, 1000);
      setError('');
    } catch { setError('文档下载未完成，请重新尝试。'); }
  }
  if(!accessValid)return <Alert type="info" message="文档访问状态已变化，请重新打开页面。"/>;
  if (!document) return <Alert type={error?'error':'info'} message={loading?'正在读取服务端正式接口文档':error||'暂不能生成此版本的调用文档'} description="调用地址来自服务端配置，不使用浏览器网址推断。" action={error?<Button onClick={()=>setRetry(value=>value+1)}>重试读取</Button>:undefined}/>;
  return <div className={styles.stack}>
    <div className={styles.header}><div><h2>{snapshot.applicationName} · {snapshot.versionId}</h2><p className={styles.muted}>文档来自服务端正式契约，绑定当前发布版本。这里展示调用方上限，实际 Key 还可能更窄。</p></div><Space wrap><Button disabled={!currentOpenApi||loading} onClick={() => download('json')}>下载 OpenAPI JSON</Button><Button disabled={!currentOpenApi||loading} onClick={() => download('yaml')}>下载 OpenAPI YAML</Button></Space></div>
    {error && <Alert type="error" message={error} action={<Button onClick={()=>setRetry(value=>value+1)}>重试读取</Button>} />}
    <div className={styles.summary}><section><h3>公共 API 基址</h3><p>{document.baseUrl}</p></section><section><h3>调用路径</h3><p>POST {document.operationPath}</p></section><section><h3>认证</h3><p>Authorization: Bearer $APP_API_KEY</p></section></div>
    <Alert type="info" message="示例在调用方服务器执行" description="先配置 APP_API_BASE_URL、APP_VERSION_ID 和 APP_API_KEY。此页面不会试调。业务参数需按下面输入结构填写；不要把永久 Key 放入浏览器或网址。" />
    <Tabs items={Object.entries(document.examples).map(([language, example]) => ({ key: language, label: { curl: 'cURL', python: 'Python', javascript: 'JavaScript' }[language], children: <pre className={styles.code}><code>{example}</code></pre> }))} />
    <section><h3>输入与最终输出结构</h3><div className={styles.summary}><section><h3>inputs</h3><pre className={styles.code}>{JSON.stringify(snapshot.inputSchema, null, 2)}</pre></section><section><h3>outputs</h3><pre className={styles.code}>{JSON.stringify(snapshot.outputSchema, null, 2)}</pre></section></div></section>
    <section><h3>发布策略与调用方上限</h3><p className={styles.muted}>实际执行还受当前 Key、系统配置与资源权限约束，取最严格范围；预留不等于供应商实际用量。</p><Table rowKey="name" size="small" pagination={false} dataSource={snapshot.limits} columns={[{ title: '项目', dataIndex: 'name' }, { title: '限制', dataIndex: 'value' }]} /></section>
    <section><h3>运行、重试与用量</h3><ul>
      <li>默认异步返回202与 run_id。wait_seconds 最多20秒，只等待同一运行；超时仍返回原 run_id。</li>
      <li>每次新业务请求设置8–128个可见 ASCII 字符的 Idempotency-Key。重试保留相同值与正文；换正文返回409。</li>
      <li>状态与最终结果按同一调用方、应用版本和外部用户分区读取。X-External-User 与 POST 的 user 同义，同时填写必须相同。</li>
      <li>外部用户标识{snapshot.externalUserMode === 'required' ? '必须提供' : '可省略并使用调用方分区'}，只用于客户分区，不赋予内部账号或数据权限。</li>
      <li>429按 Retry-After 等待。断流、未知结果或缺失 usage 不等于运行失败或零用量，勿自动新建请求或重放副作用。</li>
      {snapshot.availableScopes.includes('events.read') && <li>SSE提供真实进度和最终结果；使用 Last-Event-ID 续订同一运行。底层没有逐 token 流时不会伪造。</li>}
      {snapshot.availableScopes.includes('runs.cancel') && <li>取消使用 POST /runs/{'{run_id}'}/cancel，并受当前 Key 与资源授权控制。</li>}
      {(snapshot.availableScopes.includes('files.upload') || snapshot.availableScopes.includes('files.read')) && <li>文件使用受控文件标识，同样检查分区和来源权限；不能传本机路径、抓取地址或长期公开下载链接。</li>}
    </ul></section>
    <section><h3>错误处理</h3><p>错误响应含 error.code、中文 message 与 request_id。401为凭据问题；403为能力或当前授权不足；404不透露其他分区资源；409为版本或幂等冲突。保留 request_id 排查，避免记录认证头或客户全文。</p></section>
  </div>;
}
