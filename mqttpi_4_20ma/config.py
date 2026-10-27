"""Map mqttpi's documented ``pins[]`` analog-input schema onto ``sensors``.

mqttpi documents external-ADC inputs as pin entries::

    pins:
      - alias: gas_lel
        direction: analog_input
        source: ads1115
        bus: 1
        address: 0x48
        channel: 0
        gain: 1
        data_rate: 128
        shunt_ohms: 150          # extension: 4-20 mA loop across a shunt
        loop: 4-20mA             # extension
        fault: { low_ma: 3.6, high_ma: 21.0 }   # extension (NE43 defaults)
        publish: { interval_ms: 2000 }
        ha:
          name: "Combustible gas"
          unit_of_measurement: "%LEL"
          state_class: measurement
          scale: { min: 0, max: 100 }           # value at 4 mA / 20 mA

The sensor subsystem only polls the ``sensors`` section, so
:func:`pins_to_sensors` rewrites each such pin into a ``sensors`` entry of type
``ads1115_loop`` (this package's driver). The ``value`` entity keeps the pin's
alias, so its topics are ``{base_topic}/sensors/<alias>/state|attributes|availability``.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

DRIVER_TYPE = "ads1115_loop"

#: Pin keys copied to the sensor spec unchanged.
_DRIVER_KEYS = ("bus", "address", "channel", "gain", "data_rate", "shunt_ohms", "loop", "fault",
                "smoothing", "oversample", "clamp", "supply_v", "publish", "timeout_s", "stale_after_s", "enabled")
#: ``ha`` keys that describe the HA entity (everything else in ``ha`` except ``scale``
#: and ``precision`` is passed through to discovery as-is).
_ENTITY_ONLY = ("scale", "precision")


def is_ads1115_pin(entry: Any) -> bool:
    return (isinstance(entry, dict) and entry.get("direction") in ("analog_input", "analog")
            and str(entry.get("source", "")).lower() == "ads1115")


def pin_to_sensor(entry: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    alias = str(entry["alias"])
    ha = dict(entry.get("ha") or entry.get("homeassistant") or {})
    spec: Dict[str, Any] = {"type": DRIVER_TYPE}
    for key in _DRIVER_KEYS:
        if key in entry:
            spec[key] = copy.deepcopy(entry[key])
    if "scale" in ha:
        spec["scale"] = dict(ha["scale"])
    name = str(ha.get("name", alias.replace("_", " ").title()))
    value_ha = {k: v for k, v in ha.items() if k not in _ENTITY_ONLY}
    value_cfg: Dict[str, Any] = {"alias": alias, "ha": value_ha}
    if "precision" in ha or "precision" in entry:
        value_cfg["precision"] = ha.get("precision", entry.get("precision"))
    readings: Dict[str, Any] = {"value": value_cfg}
    if entry.get("loop"):
        readings["current"] = {"alias": f"{alias}_current", "ha": {"name": f"{name} loop current"}}
        readings["loop_fault"] = {"alias": f"{alias}_loop_fault", "ha": {"name": f"{name} loop fault"}}
    else:
        readings["voltage"] = {"alias": f"{alias}_voltage", "ha": {"name": f"{name} ADC voltage"}}
    spec["readings"] = readings
    return alias, spec


def pins_to_sensors(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``cfg`` with ``source: ads1115`` pins moved to ``sensors``.

    Other pins are left for mqttpi's GPIO subsystem. Raises ``ValueError`` when
    a pin alias collides with an existing sensor name."""
    out = copy.deepcopy(cfg)
    pins = out.get("pins") or []
    moved: List[Tuple[str, Dict[str, Any]]] = [pin_to_sensor(p) for p in pins if is_ads1115_pin(p)]
    if not moved:
        return out
    out["pins"] = [p for p in pins if not is_ads1115_pin(p)]
    sensors = out.get("sensors")
    if sensors is None:
        sensors = {}
    if isinstance(sensors, dict):
        for name, spec in moved:
            if name in sensors:
                raise ValueError(f"pins[{name}] (source: ads1115): a sensor named {name!r} already exists")
            sensors[name] = spec
    elif isinstance(sensors, list):
        existing = {str(s.get("name")) for s in sensors if isinstance(s, dict)}
        for name, spec in moved:
            if name in existing:
                raise ValueError(f"pins[{name}] (source: ads1115): a sensor named {name!r} already exists")
            sensors.append({"name": name, **spec})
    else:
        raise ValueError("sensors: expected a mapping or a list")
    out["sensors"] = sensors
    log.info("ads1115: %d analog_input pin(s) mapped to sensors: %s", len(moved), ", ".join(n for n, _ in moved))
    return out
