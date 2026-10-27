"""``LoopReading``: an mqttpi ``Reading`` that also carries JSON attributes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from mqttpi.sensors.model import Reading


@dataclass(frozen=True)
class LoopReading(Reading):
    """Extra ``attributes`` (``loop_status``, ``current_ma``...) are merged into
    each entity's ``sensors/<alias>/attributes`` JSON by
    :class:`mqttpi_4_20ma.ha.LoopAwarePublisher`. Stock mqttpi ignores them."""

    attributes: Mapping[str, Any] = field(default_factory=dict)
