from app.contracts import WorkflowDefinition, ToolBinding
from app.errors import AppError

def validate_workflow(workflow: WorkflowDefinition) -> list:
    if workflow.schemaVersion == "2.0":
        from app.runner.graph import validate_graph
        return validate_graph(workflow)
    return validate_legacy(workflow)

def validate_legacy(workflow: WorkflowDefinition) -> list:
    nodes = {node.id: node for node in workflow.nodes}
    errors = []
    if len(workflow.nodes) > 64: errors.append("At most 64 nodes are allowed")
    if workflow.version != "1.0.0": errors.append("Unsupported workflow version")
    if len(nodes) != len(workflow.nodes): errors.append("Node IDs must be unique")
    starts = [n for n in workflow.nodes if n.type == "start"]
    ends = [n for n in workflow.nodes if n.type == "end"]
    if len(starts) != 1 or len(ends) != 1: errors.append("Exactly one start and end required")
    incoming = {key: [] for key in nodes}
    outgoing = {key: [] for key in nodes}
    for edge in workflow.edges:
        if edge.source_port != "default" or edge.target_port != "default": errors.append("Legacy edges must use default ports")
        if edge.source not in nodes or edge.target not in nodes:
            errors.append("Edge references missing node")
            continue
        incoming[edge.target].append(edge.source)
        outgoing[edge.source].append(edge.target)
    for node in workflow.nodes:
        if node.type not in ("start","plugin","query","calculate","chart","report","end"): errors.append("Unsupported legacy node type: " + node.type)
        if len(incoming[node.id]) != (0 if node.type == "start" else 1): errors.append(f"{node.id}: invalid incoming degree")
        if len(outgoing[node.id]) != (0 if node.type == "end" else 1): errors.append(f"{node.id}: invalid outgoing degree")
        if node.type == "plugin":
            try:
                ToolBinding.model_validate(node.config.get("binding"))
                if not isinstance(node.config.get("arguments", {}), dict): raise ValueError()
            except Exception:
                errors.append(f"{node.id}: invalid ToolBinding/arguments")
    ordered, visited = [], set()
    current = starts[0].id if len(starts) == 1 else None
    while current is not None and current not in visited:
        visited.add(current)
        ordered.append(nodes[current])
        current = outgoing[current][0] if len(outgoing[current]) == 1 else None
    if current in visited or len(visited) != len(nodes): errors.append("Cycle or disconnected nodes")
    available_facts, available_dataset = False, False
    for node in ordered:
        if node.type in ("plugin", "query"): available_facts = True
        if node.type == "calculate":
            if not available_facts: errors.append("calculate requires plugin/query before it")
            available_dataset = True
        if node.type in ("chart", "report") and not available_dataset:
            errors.append(f"{node.type} requires calculate before it")
    if errors: raise AppError("INVALID_WORKFLOW", "Workflow validation failed", details={"errors": errors})
    return ordered
