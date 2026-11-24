"""FCS helpers shared by plugins. Reads only the header/TEXT segment (fast, even for huge files)."""
from __future__ import annotations

from dataclasses import dataclass

from flowio import FlowData


@dataclass(frozen=True)
class Channel:
    name: str   # $PnN, the value passed to R
    pns: str    # $PnS (may be empty)

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.pns})" if self.pns and self.pns != self.name else self.name


def read_channels(path: str) -> list[Channel]:
    fd = FlowData(path, only_text=True)
    return [Channel(nn, ns or "") for nn, ns in zip(fd.pnn_labels, fd.pns_labels)]


def guess_channel(channels: list[Channel], patterns: list[str]) -> str | None:
    """Case-insensitive partial match. Patterns are tried in order; for each pattern the first
    matching channel (in file order) wins. Matches against $PnN and $PnS."""
    for pat in (p.lower() for p in patterns):
        for ch in channels:
            if pat in ch.name.lower() or pat in ch.pns.lower():
                return ch.name
    return None


def markers(channels: list[Channel], exclude=()) -> list[tuple[str, str]]:
    """[(marker, channel)] from $PnS. Channels without a marker name (scatter, time...) are left out,
    as are the `exclude`d channel names. If the file has no $PnS at all, channel names are used."""
    ex = set(filter(None, exclude))
    if not any(c.pns for c in channels):
        return [(c.name, c.name) for c in channels if c.name not in ex]
    return [(c.pns, c.name) for c in channels if c.pns and c.name not in ex]
