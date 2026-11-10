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
    if len(workflow.nodes) > 64: errors.append("工作流最多允许 64 个节点")
    if workflow.version != "1.0.0": errors.append("当前工作流的兼容版本不可用")
    if len(nodes) != len(workflow.nodes): errors.append("节点标识不能重复")
    starts = [n for n in workflow.nodes if n.type == "start"]
    ends = [n for n in workflow.nodes if n.type == "end"]
    if len(starts) != 1 or len(ends) != 1: errors.append("兼容流程需要一个开始节点和一个结束节点")
    incoming = {key: [] for key in nodes}
    outgoing = {key: [] for key in nodes}
    for edge in workflow.edges:
        if edge.source_port != "default" or edge.target_port != "default": errors.append("兼容流程的连线必须使用默认端口")
        if edge.source not in nodes or edge.target not in nodes:
            errors.append("连线两端有节点不存在")
            continue
        incoming[edge.target].append(edge.source)
        outgoing[edge.source].append(edge.target)
    for node in workflow.nodes:
        if node.type not in ("start","plugin","query","calculate","chart","report","end"): errors.append("兼容流程中有不受支持的节点类型")
        if len(incoming[node.id]) != (0 if node.type == "start" else 1): errors.append("部分节点的输入连线数量不正确")
        if len(outgoing[node.id]) != (0 if node.type == "end" else 1): errors.append("部分节点的输出连线数量不正确")
        if node.type == "plugin":
            try:
                ToolBinding.model_validate(node.config.get("binding"))
                if not isinstance(node.config.get("arguments", {}), dict): raise ValueError()
            except Exception:
                errors.append("插件节点的工具绑定或参数无效")
    ordered, visited = [], set()
    current = starts[0].id if len(starts) == 1 else None
    while current is not None and current not in visited:
        visited.add(current)
        ordered.append(nodes[current])
        current = outgoing[current][0] if len(outgoing[current]) == 1 else None
    if current in visited or len(visited) != len(nodes): errors.append("连线形成环路，或存在未连接的节点")
    available_facts, available_dataset = False, False
    for node in ordered:
        if node.type in ("plugin", "query"): available_facts = True
        if node.type == "calculate":
            if not available_facts: errors.append("计算节点之前需要插件或查询节点提供数据")
            available_dataset = True
        if node.type in ("chart", "report") and not available_dataset:
            errors.append("图表或报告节点之前需要计算节点提供结果")
    if errors: raise AppError("INVALID_WORKFLOW", "；".join(dict.fromkeys(errors)), details={"errors": errors})
    return ordered
