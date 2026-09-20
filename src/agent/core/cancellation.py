"""Cooperative cancellation token (Phase 17, gui_implementation_plan.md).

Opt-in: passed by reference from a service down into `run_agent_loop()`. Every
existing caller (CLI `run_chat_turn()`/`run_ask()`, every existing test) never
constructs or passes a token, so the `cancellation_token=None` default at each
layer keeps their behavior byte-for-byte identical.

`run_agent_loop()` only ever reads `is_cancelled()` at a handful of fixed
checkpoints (before/after a provider call, before/after each tool call, before
each new loop iteration) — nothing here busy-polls in a loop. `cancel()` just
flips a thread-safe flag; the next checkpoint that happens to run afterward
sees it, at no CPU cost while waiting.
"""

from __future__ import annotations

import threading


class CancellationToken:
    """Thread-safe flag: set from the UI thread, read from a worker thread."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()
