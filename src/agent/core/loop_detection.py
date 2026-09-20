"""Phát hiện agent lặp lại cùng một hành động vô ích (Giai đoạn 6).

Chỉ so khớp (tool_name, args) — không suy luận "hành động này có nguy hiểm không"
từ nội dung, đúng nguyên tắc an toàn #1 (tier/rủi ro không được suy luận từ chuỗi
lệnh lúc runtime). Đây thuần túy là cơ chế tiết kiệm chi phí/tránh treo vòng lặp,
không phải cơ chế an toàn.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

_DEFAULT_WINDOW = 5
_REPEAT_THRESHOLD = 3


def _call_key(tool_name: str, args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"


class LoopDetector:
    def __init__(self, maxlen: int = _DEFAULT_WINDOW) -> None:
        self._calls: deque[str] = deque(maxlen=maxlen)

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        self._calls.append(_call_key(tool_name, args))

    def is_looping(self) -> bool:
        if len(self._calls) < _REPEAT_THRESHOLD:
            return False

        last = list(self._calls)[-_REPEAT_THRESHOLD:]
        return len(set(last)) == 1
