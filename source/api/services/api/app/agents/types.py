"""Compatible type views: never rewrite legacy snapshots just to add a label."""

from app.errors import AppError


def agent_type(config):
    return config.get("agent_type") or ("advanced" if config.get("main_workflow_id") else "simple")


def agent_view(config):
    kind = agent_type(config)
    if config.get("skill_bindings"):
        return {**config,"agent_type":kind,"configuration_status":"unsupported_legacy_skill_bindings","execution_supported":False,"unsupported_reason":"旧Skill绑定应用当前不受支持，原配置与历史记录保留。"}
    return {**config, "agent_type": kind, "type_inferred": not bool(config.get("agent_type")),
            "configuration_status": "needs_workflow" if kind == "advanced" and not config.get("main_workflow_id") else "configured",
            "execution_kind": "workflow" if kind == "advanced" else "assistant",
            "legacy_execution_preserved": not bool(config.get("agent_type")) and config.get("config_version", "1.0") == "1.0"}


def validate_type(config, *, executing=False):
    kind = agent_type(config)
    if kind == "simple" and config.get("main_workflow_id"):
        raise AppError("VALIDATION_ERROR", "简单类型不绑定主流程，请选择高阶类型", 422,
                       details={"field": "agent_type"})
    if executing and kind == "advanced" and not config.get("main_workflow_id"):
        raise AppError("AGENT_WORKFLOW_REQUIRED", "高阶类型尚未绑定主流程，请先保存画布并关联流程", 422,
                       details={"field": "main_workflow_id"})
