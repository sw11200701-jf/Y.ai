"""Versioned HTTP tools; all execution goes through execute_tool."""

async def execute_tool(binding, arguments, context):
    # Lazy export avoids security -> plugins.errors -> executor -> security cycles.
    from .executor import execute_tool as execute
    return await execute(binding, arguments, context)

__all__ = ["execute_tool"]
