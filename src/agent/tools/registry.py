"""Đăng ký tool kèm tier cố định lúc khai báo (nguyên tắc an toàn #1).

@tool gắn tier ngay khi module định nghĩa tool được import — không có đường nào
để tier bị suy luận lại từ tham số hay nội dung lệnh lúc agent đang chạy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.llm.base import ToolSchema
from agent.tools.schemas import Tier, ToolResult

ToolFunc = Callable[..., ToolResult]
ToolPreflight = Callable[..., object]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    tier: Tier
    parameters: dict[str, Any]
    func: ToolFunc
    preflight: ToolPreflight | None = None


_REGISTRY: dict[str, RegisteredTool] = {}


def tool(
    *,
    name: str,
    description: str,
    tier: Tier,
    parameters: dict[str, Any],
    preflight: ToolPreflight | None = None,
) -> Callable[[ToolFunc], ToolFunc]:
    def decorator(func: ToolFunc) -> ToolFunc:
        if name in _REGISTRY:
            raise ValueError(f"Tool '{name}' đã được đăng ký.")
        _REGISTRY[name] = RegisteredTool(
            name=name,
            description=description,
            tier=tier,
            parameters=parameters,
            func=func,
            preflight=preflight,
        )
        return func

    return decorator


def get_tool(name: str) -> RegisteredTool | None:
    return _REGISTRY.get(name)


def to_tool_schemas() -> list[ToolSchema]:
    return [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in _REGISTRY.values()
    ]
