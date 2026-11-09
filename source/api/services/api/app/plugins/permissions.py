"""Fresh shared RBAC checks; resource ACL is still required separately."""


def require_page(context, session, key):
    from app.rbac import require_permission
    return require_permission(context, session, key)


def require_tool_access(context, session):
    from app.rbac import require_any_permission
    return require_any_permission(context, session, ["page.plugins", "page.ai", "page.workbench"])


def require_ai_access(context, session):
    from app.rbac import require_any_permission
    return require_any_permission(context, session, ["page.ai", "page.workbench"])
