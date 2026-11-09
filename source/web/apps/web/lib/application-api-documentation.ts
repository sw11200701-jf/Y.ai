import { applicationPublicUrl } from './application-api-presentation';

export type ApplicationApiDocumentSnapshot = {
  applicationId: string; applicationName: string; kind: 'agent' | 'workflow'; versionId: string;
  clientId:string;
  /** This value must originate from the configured server public_base_url, never window/Host. */
  publicBaseUrl: string; inputSchema: Record<string, unknown>; outputSchema: Record<string, unknown>;
  publicBaseUrlStatus?: 'configured'|'development'|'not_configured';
  availableScopes: string[]; externalUserMode: 'client' | 'required'; limits: { name: string; value: string }[];
};
export function applicationApiDocumentation(snapshot: ApplicationApiDocumentSnapshot) {
  const publicBase = applicationPublicUrl(snapshot.publicBaseUrl,snapshot.publicBaseUrlStatus);
  if (!publicBase || !snapshot.applicationId || !snapshot.versionId) throw Error('发布文档缺少可信 HTTPS 公共地址或固定版本。');
  const baseUrl = publicBase.replace(/\/$/, '');
  const appPath = `/applications/${encodeURIComponent(snapshot.applicationId)}`;
  const operationPath = snapshot.kind === 'agent' ? `${appPath}/chat/messages` : `${appPath}/runs`;
  const body = snapshot.kind === 'agent' ? { message: { type: 'text', text: '<填写业务消息>' }, inputs: {}, user: '<外部用户标识>', wait_seconds: 0 } : { inputs: {}, user: '<外部用户标识>', wait_seconds: 0 };
  const jsonBody = JSON.stringify(body, null, 2);
  const examples = {
    curl: `# 在服务器配置 APP_API_BASE_URL、APP_VERSION_ID、APP_API_KEY
# 对同一次业务请求的重试，保留相同幂等值与正文。
curl --request POST "$APP_API_BASE_URL${operationPath}" \\\n  --header "Authorization: Bearer $APP_API_KEY" \\\n  --header "X-Application-Version: $APP_VERSION_ID" \\\n  --header 'Idempotency-Key: <unique-request-id>' \\\n  --header 'Content-Type: application/json' \\\n  --data '${jsonBody}'`,
    python: `import os, uuid, requests\n\nbase_url = os.environ["APP_API_BASE_URL"]\nheaders = {\n    "Authorization": "Bearer " + os.environ["APP_API_KEY"],\n    "X-Application-Version": os.environ["APP_VERSION_ID"],\n    "Idempotency-Key": str(uuid.uuid4()),\n}\npayload = ${JSON.stringify(body, null, 4)}\nresponse = requests.post(base_url + "${operationPath}", headers=headers, json=payload, timeout=30)\nresponse.raise_for_status()\nresult = response.json()\n# 202: 保存同一个 run_id，按 links.self 查询；勿另发新请求重启运行。\nprint(result["run_id"])`,
    javascript: `// 仅在调用方服务器运行，APP_API_KEY 来自服务端密钥配置。\nimport { randomUUID } from 'node:crypto';\n\nconst response = await fetch(process.env.APP_API_BASE_URL + '${operationPath}', {\n  method: 'POST',\n  headers: {\n    Authorization: 'Bearer ' + process.env.APP_API_KEY,\n    'X-Application-Version': process.env.APP_VERSION_ID,\n    'Idempotency-Key': randomUUID(),\n    'Content-Type': 'application/json',\n  },\n  body: JSON.stringify(${JSON.stringify(body, null, 2)}),\n});\nif (!response.ok) throw new Error('调用失败，HTTP ' + response.status);\nconst result = await response.json();\n// 202: 保存同一个 run_id，按 links.self 查询；勿另发新请求重启运行。\nconsole.log(result.run_id);`,
  };
  return { baseUrl, operationPath, examples };
}

/** Bind a server-generated contract to the exact version and configured URL. */
export function validateApplicationOpenApi(value:unknown,snapshot:ApplicationApiDocumentSnapshot):Record<string,unknown>{
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('服务端契约格式无效。');
  const document=value as Record<string,unknown>,servers=document.servers as {url?:unknown}[]|undefined,paths=document.paths;
  const base=applicationPublicUrl(snapshot.publicBaseUrl,snapshot.publicBaseUrlStatus)?.replace(/\/$/,'');
  const expectedOrigin=base?.endsWith('/api/open/v1')?base.slice(0,-'/api/open/v1'.length):undefined;
  if(document.openapi!=='3.1.0'||!expectedOrigin||!Array.isArray(servers)||servers.length!==1||servers[0]?.url!==expectedOrigin||!paths||typeof paths!=='object'||Array.isArray(paths))throw Error('服务端契约与当前可信地址不一致。');
  let operations=0;
  for(const [path,item] of Object.entries(paths)){
    if(!path.startsWith('/api/open/v1/')||!item||typeof item!=='object')throw Error('服务端契约路径无效。');
    if(path.startsWith('/api/open/v1/applications/')&&!path.startsWith(`/api/open/v1/applications/${encodeURIComponent(snapshot.applicationId)}/`))throw Error('服务端契约与当前应用不一致。');
    for(const [method,operation] of Object.entries(item)){
      if(!['get','post','put','patch','delete','head','options'].includes(method))continue;
      const parameters=(operation as {parameters?:{name?:string;schema?:{const?:unknown}}[]}).parameters;
      if(!Array.isArray(parameters)||!parameters.some(parameter=>parameter.name==='X-Application-Version'&&parameter.schema?.const===snapshot.versionId))throw Error('服务端契约与当前固定版本不一致。');
      operations++;
    }
  }
  if(!operations)throw Error('服务端未提供可用接口契约。');
  return document;
}
/** Read trusted metadata only from the authenticated formal-contract response. */
export function bindApplicationOpenApi(value:unknown,snapshot:ApplicationApiDocumentSnapshot){
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('服务端契约格式无效。');
  const metadata=(value as Record<string,unknown>)['x-application-contract'] as Record<string,unknown>|undefined;
  if(!metadata||metadata.application_id!==snapshot.applicationId||metadata.version_id!==snapshot.versionId||metadata.client_id!==snapshot.clientId||metadata.source!=='actual_runtime_routes_and_pydantic'||metadata.contract_version!=='v3')throw Error('正式接口文档与当前应用、版本或调用方不一致。');
  const status=metadata.public_base_url_status;
  if(typeof metadata.public_base_url!=='string'||(status!=='configured'&&status!=='development'))throw Error('尚未配置可信的服务端调用地址。');
  const bound:ApplicationApiDocumentSnapshot={...snapshot,publicBaseUrl:metadata.public_base_url,publicBaseUrlStatus:status};
  return {document:validateApplicationOpenApi(value,bound),snapshot:bound};
}

/** JSON-style quoted scalars keep schema keys and descriptions safe in exported YAML. */
export function applicationOpenApiYaml(value: unknown, depth = 0): string {
  const indent = '  '.repeat(depth);
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' && item !== null ? `${indent}-\n${applicationOpenApiYaml(item, depth + 1)}` : `${indent}- ${JSON.stringify(item)}`).join('\n') : `${indent}[]`;
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value); if (!entries.length) return `${indent}{}`;
    return entries.map(([key, item]) => {
      const compound = item !== null && typeof item === 'object';
      return compound ? `${indent}${JSON.stringify(key)}:\n${applicationOpenApiYaml(item, depth + 1)}` : `${indent}${JSON.stringify(key)}: ${JSON.stringify(item)}`;
    }).join('\n');
  }
  return `${indent}${JSON.stringify(value)}`;
}
