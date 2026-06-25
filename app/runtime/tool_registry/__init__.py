"""Built-in tool registry.

Historically a single 2,000-line module; now a package where each domain owns
its handlers and a ``register_*`` function. This ``__init__`` preserves the
original public surface so existing imports keep working:

    from app.runtime.tool_registry import (
        ToolDefinition, register, get, get_all_definitions,
        register_builtin_tools, forget_run_reads, _registry,
    )

``register_builtin_tools`` calls the domain registrars in a fixed order, which
also fixes the order tools are advertised to the model (``get_all_definitions``
iterates the registry dict in insertion order).
"""
from app.runtime.tool_registry.bash_tools import register_bash_tools
from app.runtime.tool_registry.core import (
    ToolDefinition,
    _registry,
    get,
    get_all_definitions,
    register,
)
from app.runtime.tool_registry.credential_tools import register_credential_tools
from app.runtime.tool_registry.delegation_tools import register_delegation_tools
from app.runtime.tool_registry.introspection_tools import register_introspection_tools
from app.runtime.tool_registry.kali_tools import register_kali_tools
from app.runtime.tool_registry.learning_tools import register_learning_tools
from app.runtime.tool_registry.matrix_tools import register_matrix_tools
from app.runtime.tool_registry.package_tools import register_package_tools
from app.runtime.tool_registry.schedule_tools import register_schedule_tools
from app.runtime.tool_registry.selfmod_tools import register_selfmod_tools
from app.runtime.tool_registry.steering_tools import register_steering_tools
from app.runtime.tool_registry.web_tools import register_web_tools
from app.runtime.tool_registry.workspace_tools import forget_run_reads, register_workspace_tools

__all__ = [
    "ToolDefinition",
    "register",
    "get",
    "get_all_definitions",
    "register_builtin_tools",
    "forget_run_reads",
    "_registry",
]


def register_builtin_tools():
    """Register every built-in tool. Idempotent — safe to call repeatedly."""
    register_workspace_tools()
    register_delegation_tools()
    register_selfmod_tools()
    register_steering_tools()
    register_learning_tools()
    register_web_tools()
    register_introspection_tools()
    register_schedule_tools()
    register_credential_tools()
    register_package_tools()
    register_bash_tools()
    register_matrix_tools()
    register_kali_tools()
