"""Hybrid logical clock for conflict resolution."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class HLC:
    wall_time: int
    logical: int
    node_id: str

    def to_string(self) -> str:
        return f"{self.wall_time}:{self.logical}:{self.node_id}"

    @classmethod
    def from_string(cls, value: str) -> HLC:
        parts = value.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid HLC: {value}")
        return cls(wall_time=int(parts[0]), logical=int(parts[1]), node_id=parts[2])

    def __lt__(self, other: HLC) -> bool:
        if self.wall_time != other.wall_time:
            return self.wall_time < other.wall_time
        if self.logical != other.logical:
            return self.logical < other.logical
        return self.node_id < other.node_id

    def __gt__(self, other: HLC) -> bool:
        return other < self

    def __ge__(self, other: HLC) -> bool:
        return self > other or self == other

    def __le__(self, other: HLC) -> bool:
        return self < other or self == other

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return (
            self.wall_time == other.wall_time
            and self.logical == other.logical
            and self.node_id == other.node_id
        )


class HLCClock:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._last_wall = 0
        self._last_logical = 0

    def now(self) -> HLC:
        wall = int(time.time() * 1000)
        if wall > self._last_wall:
            self._last_wall = wall
            self._last_logical = 0
        else:
            self._last_logical += 1
        return HLC(wall_time=self._last_wall, logical=self._last_logical, node_id=self.node_id)

    def receive(self, remote: HLC) -> HLC:
        wall = int(time.time() * 1000)
        max_wall = max(wall, self._last_wall, remote.wall_time)
        if max_wall == self._last_wall == remote.wall_time:
            logical = max(self._last_logical, remote.logical) + 1
        elif max_wall == self._last_wall:
            logical = self._last_logical + 1
        elif max_wall == remote.wall_time:
            logical = remote.logical + 1
        else:
            logical = 0
        self._last_wall = max_wall
        self._last_logical = logical
        return HLC(wall_time=max_wall, logical=logical, node_id=self.node_id)
