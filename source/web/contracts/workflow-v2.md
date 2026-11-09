# Platform extension contract 2.0.0 — READY FOR IMPLEMENTATION

Frozen 2026-10-16 by engineering after review of current models/module boundaries. REST prefix stays `/api/v1`; existing contract1.0.1 endpoints/payloads and stored resources remain compatible. Runtime readiness is separate and shown by node-registry status. Workflow `schemaVersion` missing or `1.0` uses original linear execution; `2.0` selects the new DAG engine. Root `version` remains definition version `1.0.0` by default, not the DSL selector. No destructive data migration.

## Routes and ownership

- Existing PluginDraft already has `endpoints[]` and `tools[]`; multiple items are first-class, no per-plugin single-endpoint restriction. GET/POST `/plugins`; GET/PUT `/plugins/{id}`; POST `/plugins/{id}/test` tests `{endpoint_id,connection_id,arguments}`; POST `/plugins/{id}/publish`; GET `/plugins/{id}/versions` and `/references`. Immutable versions freeze every endpoint/tool.03 owns routers/entity validation.
- Agent remains independent from workflows: GET/POST `/agents`, GET/PUT `/agents/{id}`, POST `/agents/{id}/debug`, POST `/agents/{id}/publish`, GET `/agents/{id}/versions`, GET `/agents/{id}/references`.03 owns draft configuration (instructions/model/bindings/input/output/limits), immutable published versions and these new routes. Debug body `{prompt,query,inputs?}`; returns a structured planning/debug result (not a fake successful run). Execution still POST `/runs` with agent_id; optional agent_version_id pins published revision (03 service extension, engineering will integrate once delivered). Existing draftAgent ids still run through existing planner.
- Workflow GET/POST `/workflows`, GET/PUT `/workflows/{id}`, POST `/workflows/validate` unchanged. NEW GET `/workflows/node-registry` returns `{version:"2.0.0",items:[NodeType],limits:{...}}`. Existing run/events/dataset/artifact routes unchanged. RunCreate adds `inputs:{...}`; completed v2 run stores `outputs`, `variable_pool` (data only; no credentials), `workflow_snapshot` and existing dataset/artifact_ids.

## DSL and variables

```json
{"schemaVersion":"2.0","name":"conditional example","input_schema":{"type":"object","properties":{"approved":{"type":"boolean"}},"required":["approved"]},"variables":{},"limits":{"max_steps":256,"timeout_seconds":120},"nodes":[{"id":"start","type":"start"},{"id":"decision","type":"if_else","inputs":{"left":{"$ref":"inputs.approved"},"right":true},"config":{"operator":"eq"}},{"id":"yes","type":"template","inputs":{"value":"ok"},"config":{"template":"{{ value }}"}},{"id":"no","type":"template","config":{"template":"rejected"}},{"id":"merge","type":"aggregate","inputs":{"values":[{"$ref":"nodes.yes.text","optional":true},{"$ref":"nodes.no.text","optional":true}]},"config":{"strategy":"first_non_null","join":"any"}},{"id":"answer","type":"answer","inputs":{"text":{"$ref":"nodes.merge.value"}}}],"edges":[{"source":"start","target":"decision"},{"source":"decision","source_port":"true","target":"yes"},{"source":"decision","source_port":"false","target":"no"},{"source":"yes","target":"merge"},{"source":"no","target":"merge"},{"source":"merge","target":"answer"}]}
```

Values are JSON literals or exact `{"$ref":"inputs.foo|nodes.node_id.output.path|vars.name|iteration.item|iteration.index|loop.state.key|loop.index","optional":false}` references. Dotted identifiers only, array indices numeric; no Python/JS evaluation, expression language, file paths or credentials. Templates substitute `{{ name }}` from resolved node inputs only; no attributes/functions/filters. Optional reference to skipped branch becomes null; required missing reference fails `VARIABLE_NOT_FOUND`. Forward/non-ancestor/cross-scope references rejected statically. Declared `variables` use `{name:{"type":"string|number|boolean|array|object|null","default":...}}`; financial numeric values remain decimal strings (number declarations accept Decimal text, not binary-float arithmetic).

Each node has `id,type,config,inputs` and optional UI metadata; edges have `source,target,source_port:"default",target_port:"default"`. One start/input; one or more terminal end/output/answer. Outer graph is acyclic/reachable; edges never encode loops. IF has exactly true/false outgoing ports (one edge each); classifying nodes have named class ports and else. Ordinary nodes have one outgoing default edge; aggregate accepts multiple incoming and one default outgoing; terminal nodes have no outgoing. Join waits for all predecessor edges to resolve; `any` (default) executes if at least one active, `all` executes only if all active, otherwise emits skipped. Skipped nodes propagate inactive edges; no failed/skipped branch output is silently fabricated.

## Repetition and limits

Iteration config `{body:WorkflowDefinition(schemaVersion2.0),max_iterations:1..50,timeout_seconds:1..60}`; input `items` array; body gets `iteration.item/index`; outputs `{items:[child.outputs...] ,count}`. Reject oversized arrays, do not silently truncate. Sequential, isolated child vars/nodes, inherited read-only outer inputs only; child nodes cannot read parent node/vars namespaces. Errors fail whole parent iteration. Parent exports child terminal outputs only.

Loop config adds `state:{...}`, `until:{left:RefOrLiteral,operator:eq|ne|gt|gte|lt|lte|contains|empty,right?:...}`; body gets loop.state/index. Body's terminal outputs become next state (must object); until evaluated after each body with updated state. Termination on condition, otherwise hitting max_iterations fails ITERATION_LIMIT (explicit count/time, not fake success). Loop output `{state,count}`. No unbounded/control-flow back-edge loops. Body nesting depth max3, global execution steps max256 (configurable1..512), graph max64 nodes, max256edges, node JSON output max1MiB and pool max4MiB, whole-run timeout<=120s, repetition timeout<=60s. Cancellation uses scheduler task cancellation; events include scoped node ids and iteration boundaries.

## Module node service (READY, keyword-only)

```python
#03 app.agents.nodes ;04 app.finance.nodes
async def execute_node(*, node_type: str, config: dict, inputs: dict,
                       context: BusinessContext, session: Session, run_id: str) -> dict:
    # {outputs: {...}, selected_port?: str, source_refs: [...], warnings: [...]}
    ...
```

03 handles `llm,agent,parameter_extractor,question_classifier`.04 handles `document_extractor,knowledge_retrieval`; core `query,calculate,chart,report` already call frozen services. query v2 input `{query?,sources?:[{type:"batch",batch_id}|{type:"plugin",binding,arguments}]}` unions sources deterministically, rejects duplicate fact keys/context mismatches through finance service. HTTP is administrator-bound plugin executor, never arbitrary URL input. NodeRegistry external handlers remain unavailable until importable; configured model nodes report needs_configuration and fail explicitly if no model. All failed services raise AppError; no fixture fallback. Node outputs are sanitized before returning; no model chain-of-thought or credential fields in variable_pool/events.

Node-registry (generated JSON + runtime GET) includes config_schema, input_schema, output_schema, ports, category, status (`supported`,`needs_configuration`,`unavailable`), reason, service owner. Code/human_approval/trigger remain unavailable: no API-process eval/shell, durable approval or scheduler trigger exists.03/04 must submit new output/config fields before changing shared contracts;07 reads registry rather than inventing node names.

Reference feature navigation only: https://docs.dify.ai/en/cloud/use-dify/nodes/agent ; ifelse/iteration/loop/variable-aggregator pages. Product code and DSL are independently implemented; no Dify runtime dependency.

## Module proposals confirmed (2026-10-16)

03 child routes APPROVED: POST `/plugins/{id}/endpoints`, PUT/DELETE `/plugins/{id}/endpoints/{endpoint_id}`; POST `/plugins/{id}/tools`, PUT/DELETE `/plugins/{id}/tools/{tool_id}`; DELETE `/plugins/{id}` archives only, published versions preserved. List query page/page_size/q/status with items+total/page/page_size and summary metadata; Endpoint operation_id (operationId input alias) unique, stable id retained. AgentWrite system_prompt/model_parameters/input_schema/tool_settings/knowledge_resources/opening_message/suggested_questions/output_schema/max_rounds/context_budget approved per03 dated request; optional agent_version_id and inputs passed through shared plan only when supplied.03 owns exact request models reflected in generated OpenAPI.

04 document routes APPROVED: POST `/documents/import` multipartfile; GET `/documents`, `/documents/capabilities`, `/documents/{file_id}`; POST `/documents/{file_id}/extract` config chunk_size/chunk_overlap; POST `/knowledge/search` body query/file_ids/top_k. Resource kinddocument scoped, original bytes private. TXT/MD/DOCX/XLSX and text-layerPDF only; no OCR/implicit latest/global search. Keyword retrieval labeledkeyword_overlap_v1, no vector claim. registry schemas now freeze04 file_id/chunk_size/chunk_overlap and query/file_ids/top_k input contracts.

Errors added: NODE_UNAVAILABLE503, VARIABLE_NOT_FOUND422, VARIABLE_TYPE_MISMATCH422, RESOURCE_LIMIT422, ITERATION_LIMIT422. Model-specific configuration errors from03 retain explicit503. Multi-dataset v2 runs track dataset_ids, while legacy dataset_id remains primary/last for compatibility; artifact download validates attached dataset ownership for each file.

Whole-node object references `{"$ref":"nodes.node_id"}` are accepted (typeobject), in addition to individual output paths;03 Agent answer can return its full structured object. JSONSchemas accept local #/$defs refs only and never fetch external schemas. Source listlimit16 and outer max_steps<=512 are canonical;04 module adaptors must follow these bounds if reused.
