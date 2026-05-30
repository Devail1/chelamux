"""Dataclasses shared across chela."""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields


def _from_dict(cls, d: dict):
    """Create a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class AgentMessage:
    """A message routed from one agent to another (via tmux or a mailbox)."""
    from_agent: str
    to_agent: str
    type: str          # message | alert | request | decision
    priority: str      # critical | high | normal | low
    ts: str            # ISO timestamp
    data: dict

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AgentMessage:
        return _from_dict(cls, d)


@dataclass
class Heartbeat:
    ts: str
    status: str        # idle | working | error

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Heartbeat:
        return _from_dict(cls, d)


@dataclass
class ScheduledTask:
    id: int
    agent_name: str
    schedule_type: str         # interval | cron | once
    schedule_value: str        # "15m" | "0 */8 * * *" | ISO timestamp
    prompt: str
    enabled: bool
    last_run: str | None
    next_run: str | None
