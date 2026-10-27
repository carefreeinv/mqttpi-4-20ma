"""Fault-aware Home Assistant publishing on top of mqttpi's sensor subsystem.

Stock mqttpi drops ``None`` values and keeps the entity's last state, which is
the wrong thing for a gas reading during an open loop: Home Assistant would keep
showing the last concentration. These subclasses add two behaviours:

* a reading key that is present but ``None`` (or NaN) marks *that entity*
  unavailable on ``sensors/<alias>/availability`` while the sensor's other
  entities (loop current, fault code) stay online;
* ``LoopReading.attributes`` are merged into ``sensors/<alias>/attributes``.

Both are small, self-contained changes intended to be proposed upstream.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from mqttpi.sensors.ha_mqtt import NumericSensorPublisher
from mqttpi.sensors.model import EntitySpec, Reading
from mqttpi.sensors.scheduler import ScheduledSensor
from mqttpi.sensors.subsystem import SensorSubsystem


class LoopAwarePublisher(NumericSensorPublisher):
    def attributes(self, reading: Reading) -> Dict[str, Any]:
        attrs: Dict[str, Any] = dict(getattr(reading, "attributes", {}) or {})
        attrs["timestamp"] = datetime.fromtimestamp(reading.timestamp, timezone.utc).isoformat(timespec="seconds")
        attrs["quality"] = reading.quality
        return attrs

    def publish_reading(self, e: EntitySpec, value: float, reading: Reading) -> None:
        self.mqtt.publish(self.state_topic(e.alias), e.format(value), retain=e.retain)
        self.mqtt.publish(self.attributes_topic(e.alias), json.dumps(self.attributes(reading)), retain=e.retain)

    @classmethod
    def wrap(cls, publisher: NumericSensorPublisher) -> "LoopAwarePublisher":
        new = cls.__new__(cls)
        new.__dict__.update(publisher.__dict__)
        return new


class FaultAwareSensorSubsystem(SensorSubsystem):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.publisher = LoopAwarePublisher.wrap(self.publisher)
        self._entity_online: Dict[str, bool] = {}

    def _set_entity(self, e: EntitySpec, online: bool) -> None:
        if self._entity_online.get(e.alias) is not online:
            self._entity_online[e.alias] = online
            self.publisher.publish_availability(e, online)

    def announce(self) -> None:
        with self._lock:
            self.publisher.publish_discovery(self.entities)
            self._entity_online.clear()
            for name, entities in self._entities.items():
                if self._online[name] and name in self._last:
                    reading, values = self._last[name]
                    self._publish_values(name, reading, values)
                else:
                    for e in entities:
                        self._set_entity(e, False)

    def _on_reading(self, s: ScheduledSensor, reading: Reading) -> None:
        values = reading.numeric()
        with self._lock:
            self._last[s.name] = (reading, values)
            self._online[s.name] = True
            self._publish_values(s.name, reading, values)

    def _publish_values(self, name: str, reading: Reading, values: Dict[str, float]) -> None:
        for e in self._entities[name]:
            if e.key in values:
                self.publisher.publish_reading(e, values[e.key], reading)
                self._set_entity(e, True)
            elif e.key in reading.values:
                # Present but None/NaN: the driver withheld this value (loop fault).
                self._set_entity(e, False)
            else:
                self._set_entity(e, True)

    def _on_stale(self, s: ScheduledSensor) -> None:
        with self._lock:
            self._online[s.name] = False
            for e in self._entities[s.name]:
                self._set_entity(e, False)
