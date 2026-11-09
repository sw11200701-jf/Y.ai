/** Additive view types for the frozen workflow-v2 contract. Unknown fields survive edits. */
export type ObjectValue = Record<string, any>;
export type Schema = { type?: string | string[]; title?: string; description?: string; enum?: any[]; properties?: Record<string, Schema>; required?: string[]; items?: Schema; default?: any; minimum?: number; maximum?: number; additionalProperties?: boolean | Schema; [key: string]: any };
export type WorkflowNode = { id: string; type: string; config: ObjectValue; inputs?: ObjectValue; ui?: { position?: { x: number; y: number }; label?: string; [key: string]: any }; [key: string]: any };
export type WorkflowEdge = { source: string; target: string; source_port?: string; target_port?: string; [key: string]: any };
export type WorkflowDefinition = { id?: string | null; name: string; version: string; schemaVersion?: '1.0' | '2.0'; mode: 'demo' | 'model'; nodes: WorkflowNode[]; edges: WorkflowEdge[]; input_schema?: Schema; variables?: ObjectValue; limits?: Record<string, number>; [key: string]: any };
export type NodeDefinition = { type: string; label: string; category: string; config_schema: Schema; input_schema: Schema; output_schema: Schema; ports: string[]; status: 'supported' | 'needs_configuration' | 'unavailable'; reason?: string | null; owner?: string; [key: string]: any };
export type NodeRegistry = { version: string; items: NodeDefinition[]; limits: Record<string, number> };
export type VariableOption = { ref: string; label: string; type: string; optional?: boolean; group?: 'inputs' | 'vars' | 'nodes' | 'system'; readonly?: boolean };
export type ReadonlyVariableCatalog = { schema_version?: string; version?: string; items: { ref: string; label: string; schema: Schema; readonly: true; source: 'server' }[] };
export type ExecutionIdentity = { kind: 'draft' | 'node_test' | 'workflow' | 'agent'; graph_sha256: string; source_graph_sha256?: string; revision?: string; workflow_id?: string; agent_id?: string; node_id?: string; scope_path?: string[]; target_key?: string };
export type NodeTrace = { node_id: string; node_type: string; status: string; inputs?: unknown; outputs?: unknown; error?: { code?: string; message?: string; request_id?: string } | null; started_at?: string; completed_at?: string; request_id?: string; reasoning?: import('./public-reasoning').TraceReasoning; sources?: unknown; technical?: ObjectValue; [key: string]: any };
export type RunTracePage = { run_id: string; execution_identity?: ExecutionIdentity; items: NodeTrace[]; last_sequence: number; has_more: boolean };
export type TestVariables = { nodes?: ObjectValue; vars?: ObjectValue; iteration?: { item: unknown; index: number }; loop?: { state: ObjectValue; index: number } };
export type DebugTarget = { source_graph_sha256: string; target_key: string; node_id: string; node_type: string; scope_path: string[]; scope_type: 'iteration' | 'loop' | null; workflow_input_schema: Schema; input_schema: Schema; output_schema: Schema; upstream_schemas: Record<string, Schema>; variables_schema: Schema; scope_schema: Schema | null; limits: Record<string, number> };
export type CheckedTestTarget = { description: DebugTarget; workflow: WorkflowDefinition };
export type DebugRunRequest = { workflow: WorkflowDefinition; agent_id?: string; revision?: string; inputs?: ObjectValue; query?: ObjectValue; prompt?: string; message?: MessageEnvelope; node_test?: { node_id: string; scope_path?: string[]; variables?: TestVariables } };
export type RunCapabilities = { is_terminal: boolean; is_active: boolean; can_cancel: boolean; can_resume: boolean; can_approve: boolean; can_reconcile: boolean };
export type RunApproval = { id: string; run_id: string; node_id: string; title: string; status: string; expires_at: string; decided_by?: string | null; decided_at?: string | null; note?: string | null };
export type ApprovalInboxItem = RunApproval & { can_approve: boolean; run_status: string };
export type ApprovalDecision = 'approve' | 'reject';
export type RunExecutionState = { run_id: string; status: string; engine?: ObjectValue; capabilities: RunCapabilities; last_sequence: number; checkpoint_id?: string | null; next_nodes?: string[]; budget?: ObjectValue; approvals?: RunApproval[] };
export type StudioRun = { id: string; status: string; engine?: ObjectValue; capabilities?: RunCapabilities; error?: ObjectValue | null; outputs?: ObjectValue; variable_pool?: ObjectValue; artifact_ids?: string[]; [key: string]: any };
export type StudioEvent = { sequence: number; type: string; run_id?: string; node_id?: string | null; status?: string | null; data?: ObjectValue; created_at?: string; [key: string]: any };
export type RunEventPage = { items: StudioEvent[]; last_sequence?: number; has_more?: boolean };
export type StudioResources = { agents: ObjectValue[]; agentVersions: ObjectValue[]; models: ObjectValue[]; documents: ObjectValue[]; plugins: ObjectValue[]; versions: ObjectValue[]; connections: ObjectValue[]; imports: ObjectValue[]; knowledgeBases?: ObjectValue[]; knowledgeVersions?: ObjectValue[]; databases?: ObjectValue[]; dataTables?: ObjectValue[]; tableVersions?: ObjectValue[]; warnings: string[] };
export type MessageKind = 'text' | 'image' | 'audio' | 'file' | 'mixed';
export type AttachmentMetadata = { id: string; filename: string; mime: string; size_bytes: number; media_kind: 'image' | 'audio' | 'document'; sha256?: string };
export type MessageEnvelope = { type: MessageKind; text?: string; attachment_ids?: string[]; inputs?: ObjectValue };
export type MessageDraft = { type: MessageKind; text: string; attachments: AttachmentMetadata[] };
export type WorkflowLeaveGuard = () => boolean | Promise<boolean>;
export type WorkflowStudioProps = {
  /** File interchange transport is supplied by the host after its contract is loaded. */
  fileClient?: import('../workflow-file-client').WorkflowFileClient;
  /** Opens a new standalone draft; must never change the current Agent binding. */
  onWorkflowImported?: (workflow: WorkflowDefinition) => void | Promise<void>;
  createOnOpen?:boolean; initialMessageKind?:MessageKind; initialAgent?:ObjectValue; embedded?: boolean; agentId?: string; agentName?: string; workflowId?: string | null;
  /** Fill the parent's remaining viewport. The parent must provide a definite height. */
  fullPage?: boolean; agentType?: 'simple' | 'advanced'; onBack?: () => void;
  /** Parent navigation must call this guard before changing URL or selected Agent. */
  onLeaveGuardChange?: (guard: WorkflowLeaveGuard | null) => void;
  /** Opaque UI epoch only. Never sent to the workflow/model or used as authorization. */
  identityKey?: string; publishedVersion?: string | number | ObjectValue;
  onWorkflowChange?: (id: string) => void; onDirtyChange?: (dirty: boolean) => void;
  /** True when host navigation callbacks await the registered guard; avoids a second dialog. */
  parentGuardsNavigation?: boolean;
  canManageModels?: boolean;
  /** Supplied by an authorized host; opens API management for a saved asset, without publishing. */
  onOpenApplicationApi?: (source: import('../application-api-presentation').ApplicationAssetSelection) => void | Promise<void>;
  onPublish?: () => Promise<unknown>; onNavigate?: (page: string) => void | Promise<void>;
};
