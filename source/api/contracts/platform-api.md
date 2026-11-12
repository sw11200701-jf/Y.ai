# Yai 平台 API v1

工作流、Agent、知识库和结构化数据库共用平台密钥及空间授权。以下路径以 `/api/platform/v1` 为前缀。生产环境使用部署方提供的 HTTPS 地址。

## 1. 凭据与权限

在「平台 API 与空间」新建平台密钥，选择空间、资源及访问级别。调用者后端通过 `POST /token` 交换短令牌，后续请求使用 `Authorization: Bearer <access_token>`。令牌有效期为 900 秒。密钥仅在创建或轮换时展示，宜存入调用方的密钥管理系统。

| 用途 | 授权 |
| --- | --- |
| 目录、详情、切片和检索 | 空间及相关知识库、文档、版本和原件的读取授权。行列限制继续生效 |
| 工作流、Agent 调用 | 来源资源可读取并允许执行。已发布的 Agent 版本和运行依赖也需授权 |
| 修改、发布、删除或恢复 | 相关资源写入授权及完整维护范围 |
| 创建知识库、数据库、表、批次或版本 | 空间可写并勾选「允许创建」，同时具备已有目标和相关来源的维护权限 |
| 下载原件及完整 XLSX | 原件下载或完整明细导出授权 |

平台密钥的空间与资源范围、令牌签发范围、密钥主体和发放人的当前权限同时生效。对接时可以先用 `GET /spaces` 和 `GET /spaces/{workspace_id}/resources?kind=knowledge_base` 核对目录。对同一集成持续使用同一平台密钥，可以用新换取的短令牌继续查询其运行。

通用资源目录支持按类型查询，常用值为 `workflow`、`agent`、`knowledge_base`、`database` 和 `data_table`。使用 `GET /spaces/{workspace_id}/resources/{kind}/{identifier}` 获取受支持的资源详情。附件通过 `POST /spaces/{workspace_id}/attachments` 上传，表单字段为 `file`。密钥轮换、空间成员和角色配置、工作流及 Agent 的 API 发布，由管理入口使用登录会话和相应权限操作。

下列示例使用 Bash、cURL 和 jq。先在调用方后端设置 `BASE_URL` 与 `PLATFORM_API_KEY`。

```bash
API="$BASE_URL/api/platform/v1"
TOKEN=$(jq -n --arg key "$PLATFORM_API_KEY" '{api_key:$key}' |
  curl --fail-with-body -sS -H 'Content-Type: application/json' \
    --data-binary @- "$API/token" | jq -er '.access_token')
curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$API/spaces"
# 将接口返回的 id 赋给 WORKSPACE_ID
SPACE="$API/spaces/$WORKSPACE_ID"
```

`POST /token` 可提交可选的 `workspace_ids`，只交换指定空间。令牌可用 `POST /token/revoke` 撤销。应用 API 的 `/api/open/v1` 和平台 API 的令牌由各自的入口签发。

## 2. 调用工作流和 Agent

在 AI 工作室打开资源的「API 接入配置」并发布固定 API 版本。工作流使用已保存的 2.0 定义，Agent 需先发布 Agent 版本，再把该版本绑定到 API 发布。页面会提供发布 ID、输入输出结构和调用示例。

### 工作台发布与空间内使用

资源所有者可从 AI 工作室将智能体或独立工作流发布到工作台。空间公开适用于当前空间的成员。部分公开可按成员或角色选择，命中任一项即可使用。私有仅资源所有者可使用。工作台执行发布时保存的快照，模型、工具、知识库和数据表仍按调用者权限核验。带工作流的智能体会一起保存并发布主流程快照，工作台通过该智能体执行主流程。

以下工作台管理接口使用登录会话和 CSRF。写入时传当前公开范围的 `expected_revision`，首次为 0。工作流发布另需从详情读取 `expected_graph_sha256` 所对应的 `graph_sha256`。发布列表和详情返回 `publication`，包含状态、公开范围、版本和修订号。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/v1/agents/publication-audience` | 获取当前空间成员和角色选项 |
| `POST /api/v1/agents/{agent_id}/publish` | 发布智能体。JSON 字段 `visibility` 取 `public`、`partial` 或 `private`，部分公开时提供 `user_ids`、`role_ids` 中至少一项 |
| `POST /api/v1/workflows/{workflow_id}/publish` | 发布独立工作流。使用同样的公开字段，另传 `expected_graph_sha256` |
| `GET /api/v1/agents/conversations` | 获取当前账号可在工作台选择的已发布智能体及工作流 |
| `POST /api/v1/runs` | 传 `workflow_id` 运行工作流，或传 `agent_id` 和 `agent_version_id` 运行智能体。可附 `message` 和 `inputs` |

既有资源首次设置公开范围前沿用原授权。工作台公开范围也约束平台 API 的账号访问。已设置公开范围的资源通过具备空间成员身份、执行授权和固定 API 发布的平台代理令牌接入。

### 固定 API 版本

| 方法与路径 | 用途 |
| --- | --- |
| `GET /spaces/{workspace_id}/publications/{publication_id}` | 读取该发布版本及 `input_schema`、`output_schema` |
| `POST /spaces/{workspace_id}/publications/{publication_id}/invoke` | 提交工作流或 Agent，返回 `202`、`run_id`、`status` 和 `result_url` |
| `GET /spaces/{workspace_id}/runs/{run_id}` | 查询状态和已成功运行的 `outputs` |
| `GET /spaces/{workspace_id}/files/{file_id}` | 读取经发布策略授权的生成文件信息 |
| `GET /spaces/{workspace_id}/files/{file_id}/download` | 下载授权生成文件 |

`invoke` 请求接受 `parameters` 对象、可选的 `prompt` 及 `message`。参数以该发布版本的 `input_schema` 为准。每个新业务请求需要一个 8–128 个可见 ASCII 字符的 `Idempotency-Key`。遇到网络超时，使用相同键和相同请求正文重试，服务返回原运行并标记 `replayed`。同一个键提交不同正文返回 `409`。处于 `outcome_unknown` 的运行需核对原运行记录。

```bash
RUN_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $REQUEST_IDEMPOTENCY_KEY" -H 'Content-Type: application/json' \
  --data '{"parameters":{},"prompt":"执行本次任务"}' \
  "$SPACE/publications/$PUBLICATION_ID/invoke" | jq -er '.run_id')
curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$SPACE/runs/$RUN_ID"
```

## 3. 知识库与文档

本节路径相对 `/spaces/{workspace_id}`。列表支持 `offset` 和 `limit`，默认每页 20 条，最多 100 条。

| 方法与路径 | 请求及结果 |
| --- | --- |
| `GET /knowledge/capabilities` | 当前部署支持的上传格式、大小、规则解析和检索能力 |
| `GET /knowledge-bases` | 分页列出可读取的知识库 |
| `POST /knowledge-bases` | JSON `name`、可选 `description`。返回知识库 ID 和 `ETag` |
| `GET /knowledge-bases/{kb_id}` | 读取知识库和 `ETag` |
| `PATCH /knowledge-bases/{kb_id}` | 发送 `If-Match`，修改 `name` 或 `description` |
| `GET /knowledge-bases/{kb_id}/documents` | 分页列出知识库中的授权文档 |
| `POST /knowledge-bases/{kb_id}/documents` | 普通上传，multipart 表单字段 `file` |
| `GET /knowledge/documents/{document_id}` | 返回文档、原件 `file_id`、草稿与发布版本 ID，以及 `ETag` |
| `PATCH /knowledge/documents/{document_id}` | 发送 `If-Match`，JSON `name` 修改显示名称 |
| `GET /knowledge-bases/{kb_id}/documents/by-external-id/{external_id}` | 用业务系统的文档 ID 查询和读取 `ETag` |
| `PUT /knowledge-bases/{kb_id}/documents/by-external-id/{external_id}` | 按业务 ID 首次创建或更新原件，multipart 字段 `file` |
| `PUT /knowledge/documents/{document_id}/original` | 按 Yai 文档 ID 更新原件，multipart 字段 `file` |
| `GET /knowledge/files/{file_id}/download` | 下载有权限的当前或历史原件 |
| `DELETE /knowledge/documents/{document_id}` / `DELETE /knowledge-bases/{kb_id}` | 移入回收站 |
| `POST /knowledge/documents/{document_id}/restore` / `POST /knowledge-bases/{kb_id}/restore` | 从回收站恢复 |
| `GET /knowledge/recycle-bin` | 列出可维护的已删除知识库。传 `kb_id` 时列出指定库中的已删除资料 |

### 按同一业务 ID 覆盖

`external_id` 在同一知识库内唯一，支持 1–128 字符，首字符须为 ASCII 字母或数字，其余可含字母、数字、`_`、`-`、`.` 和 `:`。首次上传返回 `201` 并创建稳定的 `document_id`。以后要更新内容时，先读取当前文档，将响应头中的完整 `ETag` 连同引号传给 `If-Match`。

同 ID、同文件名、同字节、同上传方式的重试返回 `200` 和 `changed: false`。内容变化时使用最新 `If-Match`；缺少时返回 `428`，过期时返回 `412`。覆盖成功后 `document_id` 保持一致，`file_id` 更新，历史原件与知识版本保留。旧的已发布知识继续用于检索，直到新原件生成新草稿并发布。处理中或结果待核查的语义任务会阻止覆盖，返回 `409`。

```bash
DOC_URL="$SPACE/knowledge-bases/$KB_ID/documents/by-external-id/finance-policy-42"
curl --fail-with-body -sS -D create.headers -X PUT -H "Authorization: Bearer $TOKEN" \
  -F 'file=@policy.txt' "$DOC_URL"

# 每次内容变更前先读取当前文档
curl --fail-with-body -sS -D current.headers -o current.json \
  -H "Authorization: Bearer $TOKEN" "$DOC_URL"
ETAG=$(awk 'tolower($1)=="etag:" {gsub(/\r/,"",$2); print $2}' current.headers)
curl --fail-with-body -sS -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "If-Match: $ETAG" -F 'file=@policy-v2.txt;filename=policy.txt' "$DOC_URL"
```

普通上传默认 `profile=legacy`，上限 5 MiB。同 ID 上传及覆盖默认 `profile=portable-slicing-1.0.0`，上限 40 MiB。可在上传路径使用 `?profile=` 显式选择。格式及旧 Office 原件支持由当前部署的 `capabilities` 返回。

### 解析、修订、发布和检索

| 方法与路径 | 请求及结果 |
| --- | --- |
| `POST /knowledge/documents/{document_id}/parse` | JSON `strategy` 可取 `length` 或 `paragraph`，可选 `chunk_size` 与 `chunk_overlap`。返回新草稿版本 ID |
| `GET /knowledge/documents/{document_id}/versions` | 分页读取历史版本与对应的 `file_id` |
| `GET /knowledge/documents/{document_id}/chunks` | 可传 `version_id`、`offset`、`limit`、`q` 和 `enabled` |
| `POST /knowledge/documents/{document_id}/revisions` | 提交最新 `base_version_id` 及 `corrections`、`additions`、`deletions` 中至少一项。返回新草稿版本 ID |
| `POST /knowledge/documents/{document_id}/publish` | JSON `version_id`，必须是当前原件的最新有效知识版本 |
| `GET /knowledge/documents/{document_id}/chunks/latest.xlsx` | 必传 `expected_version_id`。默认列为「序号、语义、原文段落」，可用 `columns` 选择附加字段 |
| `POST /knowledge/search` | JSON `query`、`knowledge_base_ids`，可选 `file_ids`、`version_ids`、`top_k`（1–20）。返回命中及来源 |

规则解析适用于运行实例报告的 `rule_formats`。需要语义切片的 PPT 和其他版面文件可先通过 API 入库，在知识库页面处理成草稿后，通过 API 编辑、发布和检索。

```bash
D="$SPACE/knowledge/documents/$DOCUMENT_ID"
VERSION_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data '{"strategy":"paragraph"}' "$D/parse" | jq -er '.id')
curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$D/chunks"
jq -n --arg version "$VERSION_ID" '{version_id:$version}' |
  curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @- "$D/publish"
jq -n --arg kb "$KB_ID" --arg query '报销审批要求' '{knowledge_base_ids:[$kb],query:$query,top_k:5}' |
  curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @- "$SPACE/knowledge/search"
```

修订示例中的 `chunk_id` 来自当前知识版本的切片列表。新增知识使用 `source_chunk_id` 标明原有来源。删除项是当前切片 ID 数组。每次修订会生成新知识版本，返回的新 ID 用于后续发布或导出。

```json
{
  "base_version_id": "<当前草稿版本 ID>",
  "corrections": [{"chunk_id": "<当前切片 ID>", "text": "更新后的知识正文", "enabled": true}],
  "additions": [],
  "deletions": []
}
```

### 单条知识的查询和维护

从切片列表或文档检索结果取得 `knowledge_id`，并使用该条知识所属的 `document_id`。检索匹配语义概括、关键词和原文，响应中的 `semantic` 与 `original_paragraph` 分别提供概括和对应的原文。导出时可选「知识ID」附加列。文档检索命中的 `resource_id` 是文档 ID。修改同一条知识时，`knowledge_id` 跨人工修订保持一致；`item.id` 是当前版本内的切片 ID。整份重新切片可能产生新的知识条目和标识。

以下路径相对 `/knowledge/documents/{document_id}`。

| 方法与路径 | 请求及结果 |
| --- | --- |
| `GET /items/{knowledge_id}` | 读取最新保存版本中的单条知识，返回 `version_id` 和 `item`。传 `version_id` 可读取已发布或指定的历史版本 |
| `POST /items` | 发送 `base_version_id`、`source_knowledge_id`、`text`，可选 `topic`、`enabled`。基于现有知识的来源新增一条，返回其新 ID |
| `PATCH /items/{knowledge_id}` | 发送 `base_version_id`，并提供 `text`、`topic` 或 `enabled` 中至少一项。将 `enabled` 设为 `false` 可停用 |
| `DELETE /items/{knowledge_id}?base_version_id=…` | 从新草稿移除指定知识。已保存的历史版本仍可按版本读取 |

写入需有文档的完整维护权限及创建知识版本的授权。每次写入返回新的 `version_id` 和 `requires_publish: true`。提交过期的 `base_version_id` 返回 `409`。发布新版本后，检索才使用这次修改、启停或删除的结果。文档至少保留一条知识；若整份内容都应退出检索，可停用条目或将文档移入回收站。

```bash
ITEM="$SPACE/knowledge/documents/$DOCUMENT_ID/items/$KNOWLEDGE_ID"
VERSION_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$ITEM" |
  jq -er '.version_id')
jq -n --arg version "$VERSION_ID" --arg text '更新后的知识正文' \
  '{base_version_id:$version,text:$text}' |
  curl --fail-with-body -sS -X PATCH -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @- "$ITEM"
```

## 4. 数据库与数据表

本节路径相对 `/spaces/{workspace_id}`。数据库是 Yai 内用于管理结构化数据的业务资源。查询提交受控条件，接口使用空间权限、行列授权和已发布数据版本。

| 方法与路径 | 请求及结果 |
| --- | --- |
| `GET /databases` | 分页列出可读数据库，支持 `offset`、`limit` |
| `POST /databases` | JSON `name`、可选 `description`，返回库 ID 和 `ETag` |
| `GET /databases/{database_id}` | 读取库详情和 `ETag` |
| `PATCH /databases/{database_id}` | JSON `name` 或 `description`，发送 `If-Match` |
| `GET /databases/{database_id}/tables` | 列出该库内已授权的数据表 |
| `POST /databases/{database_id}/tables` | 声明 `name`、`schema_fields`，可选 `description`、`mapping`、`quality_rules`。创建空表并返回 `ETag` |
| `GET /database/tables/{table_id}` | 读取表详情、当前发布版本 ID、草稿批次 ID 和 `ETag` |
| `PATCH /database/tables/{table_id}` | JSON `name` 或 `description`，发送 `If-Match` |
| `DELETE /databases/{database_id}` 或 `DELETE /database/tables/{table_id}` | 发送 `If-Match`，移入回收站，返回删除后的 `ETag` |
| `GET /database/recycle-bin` | 分页列出可维护的已删除库。传 `database_id` 查看该有效库中的已删除表。每项含 `etag` |
| `POST /databases/{database_id}/restore` 或 `POST /database/tables/{table_id}/restore` | 用回收站中的 `etag` 作为 `If-Match`，恢复原库或表 |

首次删除须匹配当前 `ETag`，针对仍在回收站的同一资源重试会返回已有删除记录。删除后，相关表、历史版本、批次与原件暂停访问。恢复保留原 ID、原件、草稿和已发布版本。库内独立删除过的表保持删除状态，可在库恢复后单独恢复。删库和恢复库须具备库内所有有效表的完整维护权限。旧版知识表通过其原知识库维护；绑定了此类表的库受回收站保护。

### 导入、查询和导出

| 方法与路径 | 请求及结果 |
| --- | --- |
| `POST /databases/{database_id}/imports` | 上传 CSV 或 XLSX，自动推断字段、新建表并生成待发布批次。表单字段为 `file`，可选 `name`、`sheet_name` |
| `POST /database/tables/{table_id}/imports` | 向同一表导入新文件，保留表 ID。表单字段为 `file`，可选 `sheet_name` |
| `GET /database/tables/{table_id}/batches` | 完整维护人员查看导入及修订批次 |
| `GET /database/batches/{batch_id}` | 完整维护人员查看质量报告和有限预览 |
| `POST /database/batches/{batch_id}/revisions` | JSON `corrections` 按原文件行号和列名修订单元格，可提交 `schema_fields`、`mapping`、`quality_rules`、`cleaning` |
| `POST /database/batches/{batch_id}/publish` | 发布通过校验的批次并将它设为当前版本，返回版本 ID |
| `GET /database/tables/{table_id}/versions` | 列出已发布且已授权的版本 |
| `GET /database/tables/{table_id}/dictionary` | 读取当前或指定 `version_id` 的授权字段字典 |
| `POST /database/tables/{table_id}/query` | JSON 可选 `version_id` 和 `query`，支持筛选、分页和聚合，遵守行列权限 |
| `GET /database/files/{file_id}/download` | 下载具备完整权限的导入原件，`file_id` 来自导入批次 |
| `POST /database/tables/{table_id}/export` | 导出当前或指定 `version_id` 的授权明细 CSV，响应头含 `X-Data-Version` |

导入文件上限为 5 MiB。导入或修订生成草稿，发布后才更新默认查询和导出。单元格修订示例为 `{"corrections":[{"row":2,"column":"amount","value":120}]}`，第 1 行为表头。整行新增或删除可提交包含目标行集的新文件，向同一表导入并发布。原件及历史发布版本仍可在授权范围内读取。

```bash
DB_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data '{"name":"财务台账"}' "$SPACE/databases" | jq -er '.id')
IMPORTED=$(curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
  -F 'file=@ledger.csv' "$SPACE/databases/$DB_ID/imports")
TABLE_ID=$(jq -er '.table.id' <<<"$IMPORTED")
BATCH_ID=$(jq -er '.batch.id' <<<"$IMPORTED")
curl --fail-with-body -sS -X POST -H "Authorization: Bearer $TOKEN" \
  "$SPACE/database/batches/$BATCH_ID/publish"
curl --fail-with-body -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"query":{"limit":20}}' "$SPACE/database/tables/$TABLE_ID/query"
```

## 5. 错误与机器可读文档

接口错误包含 `error.code`、`error.message` 和 `request_id`。排障时保留 `request_id`，超级管理员可在「系统日志」按编号查找已记录的请求。

| 状态 | 含义 |
| --- | --- |
| 401 | 凭据无效、已撤销或过期 |
| 403 / 404 | 权限不足或资源对当前令牌不可见 |
| 409 | 幂等冲突、知识版本冲突、发布依赖变化或任务未结 |
| 412 / 428 | 资源 ETag 已变化 / 缺少必要的 If-Match |
| 413 / 422 | 文件大小或参数、格式与版本校验未通过 |
| 503 | 服务暂不可用。提交请求若提示结果待确认，先查询原运行或按幂等规则重试 |

在「平台 API 与空间」下载 OpenAPI JSON，或携带平台 Bearer 令牌读取 `GET /api/platform/v1/openapi.json`。具体工作流和 Agent 的业务输入输出以各自发布版本返回的 Schema 为准。
