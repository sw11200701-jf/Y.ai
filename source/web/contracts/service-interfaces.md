# Service interfaces v1.0.0 — READY

Core financial/tool signatures below remain compatible in platform2.0.0. Node service execute_node and additive Agent plan inputs/agent_version_id are frozen in workflow-v2.md; canonical node schemas in node-registry.json and actual registry route.

Transaction clarification (2026-10-17): app.main middleware owns request Session commit/rollback before response headers; get_session injects that same Session. save_resource remains flush-only so imports/multi-resource writes stay atomic. Existing explicit commits are compatible. Background runner tasks always open their own SessionLocal. Standalone uses of get_session without app middleware retain the legacy generator lifecycle. No module router edits are required for the response-before-commit fix.

Modules export `router` (APIRouter without prefix, main mounts under `/api/v1`) and these async functions. All calls use keyword arguments. Use shared Pydantic types from `app.contracts`, `Session` from SQLAlchemy, persistence helpers from `app.db`. JSON-compatible dict results may be accepted at boundary and validated by runner. Raise `app.errors.AppError(code, message, status_code=422, details=None)` for expected errors. Never swallow failures or replace them with fixture success.

```python
# app.plugins.service
async def execute_tool(*, binding: ToolBinding, arguments: dict,
                       context: BusinessContext, session: Session) -> ToolExecutionResult: ...
# app.agents.service
async def plan(*, agent_id: str, prompt: str, query: FactQuery,
               context: BusinessContext, session: Session) -> WorkflowDefinition: ...
# app.imports.service
async def import_file(*, filename: str, content: bytes,
                      context: BusinessContext, session: Session) -> dict: ...
# app.finance.service
async def query_facts(*, query: FactQuery, context: BusinessContext,
                      session: Session) -> list[FinancialFact]: ...
async def calculate_dataset(*, facts: list[FinancialFact], query: FactQuery,
                            context: BusinessContext, session: Session) -> ResultDataset: ...
# app.reporting.service
async def generate_artifacts(*, dataset: ResultDataset, run_id: str,
                             context: BusinessContext, session: Session) -> list[Artifact]: ...
```

`execute_tool` returns facts in `facts`; raw sanitized body may be in `data`, source refs in `source_refs`. Plugin must map HTTP finance responses into FinancialFact. Runner passes plugin facts to calculate_dataset (or query_facts for Excel path); it never fabricates facts. `plan` returns WorkflowDefinition with concrete frozen ToolBinding in plugin node config. Demo mode supports a documented intent only and returns `mode=demo`.

Persistence: `save_resource(session, kind, resource_id, payload, context)` upserts tenant/workspace scoped JSON; `get_resource(session, kind, resource_id, context)` returns dict or raises NOT_FOUND; `list_resources(session, kind, context)` returns list; `new_id(prefix)` generates IDs; `utc_now()` ISO timestamp. Helpers flush; caller commits API mutations; runner commits after every node. Resource kinds: plugin, plugin_version, connection, agent, import_batch, fact, dataset, workflow, run, artifact. Financial amounts remain strings in JSON/TEXT. For secrets use `app.secrets.store_secret/read_secret`; never place plaintext credentials in connection payload, logs, or return bodies. Engineering owns Resource and RunEventRecord models; request extra database columns here before editing.

Artifacts: reporting writes only inside `app.settings.settings.artifact_dir` and returns absolute `path` to backend; public API strips `path`. Include id, run_id, dataset_id, type (`docx`/`xlsx`), filename, sha256, size_bytes, created_at. Runner persists returned artifacts and dataset even if service already saved them. Report uses the dataset's snapshot_id throughout.

Routers obtain `session: Session = Depends(get_session)` from app.db and `context: BusinessContext = Depends(get_context)` from app.context. Non-module runner endpoints live in app.runner.router. Module imports must not create files or perform requests. Add dependency requirements to coordination requests; engineering installs and locks centrally.
