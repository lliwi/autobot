"""Core registry primitives for built-in tools.

The registry itself is intentionally tiny: a name→``ToolDefinition`` dict plus
``register``/``get``/``get_all_definitions``. Each domain module under this
package owns its handlers and a ``register_*`` function that populates the
registry; :func:`app.runtime.tool_registry.register_builtin_tools` wires them
together in a stable order.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: Callable


_registry: dict[str, ToolDefinition] = {}


def register(tool_def: ToolDefinition):
    _registry[tool_def.name] = tool_def


def get(name: str) -> ToolDefinition | None:
    return _registry.get(name)


def get_all_definitions() -> list[dict]:
    """Return tools in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for td in _registry.values()
    ]
