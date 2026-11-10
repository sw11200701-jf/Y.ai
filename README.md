# Y.ai · 章谱普通版

章谱提供知识库、文档语义切片、数据治理、智能体、工作流、插件和应用 API。本仓库从 2026-11-09 已发布的普通版源码整理，包含独立的开发环境和合成测试。

## 启动开发环境

需要 Docker 和 Docker Compose v2。首次构建会下载锁定的 Python 和 Node 依赖，以及 Office 转换用的 Debian 软件包。

```bash
docker compose up --build --wait
```

打开 [本机章谱开发环境](http://127.0.0.1:3271/)，按页面提示创建本机开发管理员。开发环境使用独立的 SQLite 数据卷和登录 Cookie。复制 `.env.example` 为 `.env` 后，可用 `ZHANGPU_WEB_PORT` 更改本机网页端口。每位开发者使用自己的测试资料和单独配置的模型凭据。

```bash
docker compose ps
docker compose logs --tail 80 api web office
docker compose down
```

下次启动会继续使用开发数据。改完源码后重新执行 `docker compose up --build --wait`。

## 目录和协作

| 路径 | 用途 |
|---|---|
| `source/api/services/api/app` | FastAPI 服务、账号权限、知识库、工作流和模型调用 |
| `source/api/contracts` | API 和工作流契约 |
| `source/web/apps/web` | Next.js 页面和前端测试 |
| `source/web/contracts` | 前端使用的契约快照 |
| `converter` | DOC 及旧版 Office、ODF 原件的隔离转换 |
| `docker`、`compose.yaml` | 本机开发镜像和服务编排 |

日常修改通过功能分支和 Pull Request 合并。PR 说明影响的业务入口及验证方式。修改工作流或数据权限时，同步核对服务端契约和前端行为。用合成资料复现问题。

## 验证

Docker 构建时运行前端工作台测试和 Next.js 构建。服务启动后，可以使用 Python 3 运行只读冒烟检查。

```bash
python tests/smoke.py http://127.0.0.1:3271
```

在空白开发数据卷上，可以用 `--fresh-fixture` 创建临时验收账号和资料，检查上传格式、分流和原件下载。该选项只接受回环地址，并要求实例还未初始化。

模型切片要在应用的模型管理中配置获准的开发模型。冒烟检查只验证本机服务和文件流程。Office 转换服务通过 Unix 套接字接收原件，容器无网络，服务器无需另外安装 OCR。

## 部署边界

`compose.yaml` 是回环地址的开发配置。生产部署需要 HTTPS、持久存储、部署凭据和独立发布流程。生产保全校验使用数据目录中的私有 `retention-pins.json`，由部署方单独配置。仓库内只保留校验逻辑与字段结构。

数据库、客户原件、真实任务记录、模型密钥、证书私钥、日志和镜像归档在仓库外管理。线上账号和工作空间不会随仓库复制。

### 会话和密钥

控制台角色默认登录有效期为 8 小时，空闲期限为 30 分钟。角色管理员可在会话规则页调整。旧版长期管理员会话在下次校验时按当前规则计算有效期，过期后需重新登录。

部署 API 时可用 `FINANCE_SECRET_KEY_FILE` 指定容器内可读的绝对路径。已有实例迁移时，将原数据目录 `secrets/local.key` 的**同一把密钥**复制到该路径，并与数据分开备份。若指定的密钥无法解密现有凭据，服务会拒绝启动。开发 Compose 默认使用数据目录中的本地密钥。

外部插件和服务连接的地址清单由部署配置 `TOOL_ALLOWED_ORIGINS` 管理。填写逗号分隔的精确源地址，包含协议、主机和非默认端口，更新部署配置后重启 API。超级管理员可在「插件与服务」页启用部署允许范围内的地址，保存后即时约束后续工具调用。

运行记录页按 `RUN_RETENTION_DAYS`（默认 90 天）展示当前账号的超期记录概况。待核查、仍有未确认副作用或显式保全的记录单独统计。工作流和智能体的回收站保留原配置和版本，通过恢复重新启用。
