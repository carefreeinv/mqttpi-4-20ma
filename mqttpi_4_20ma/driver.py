"""``ads1115`` sensor driver for mqttpi: one ADS1115 input, in volts or as a
4-20 mA current loop measured across a precision shunt.

Pipeline per read::

    raw counts --(PGA full scale)--> volts --(shunt_ohms)--> mA --(scale)--> value

Readings (keys of the returned ``Reading``):

* ``value``: the scaled engineering value (``None`` on a loop fault);
* ``current``: loop current in mA (loop mode only, always published);
* ``loop_fault``: 0 ok, 1 under range, 2 over range, 3 fault low (open loop),
  4 fault high (loop mode only);
* ``voltage``: volts at the ADC input (voltage mode only).

Config keys follow mqttpi's documented ``analog_input`` / ``source: ads1115``
schema (``bus``, ``address``, ``channel``, ``gain``, ``data_rate``, ``scale`` or
``ha.scale``) plus the compatible extensions ``shunt_ohms``, ``loop: 4-20mA``,
``fault: {low_ma, high_ma}``, ``smoothing``, ``oversample``, ``clamp`` and
``supply_v``.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Mapping, Optional

from mqttpi.sensors.i2c import parse_address
from mqttpi.sensors.model import QUALITY_GOOD, QUALITY_UNCERTAIN

from mqttpi_4_20ma.ads1115 import (
    CODE_MAX,
    CODE_MIN,
    Ads1115,
    BusRef,
    Sleep,
    counts_to_volts,
    full_scale_volts,
    parse_channel,
    parse_data_rate,
    parse_gain,
)
from mqttpi_4_20ma.loop import (
    FAULTS,
    STATUS_CODES,
    STATUS_FAULT_HIGH,
    STATUS_OK,
    Ema,
    LinearScale,
    LoopThresholds,
    ma_to_volts,
    volts_to_ma,
)
from mqttpi_4_20ma.reading import LoopReading

log = logging.getLogger(__name__)

LOOP_4_20 = "4-20mA"
_LOOP_SPELLINGS = {"4-20ma", "4_20ma", "4-20", "4..20ma", "current_loop"}

_CURRENT = {"unit_of_measurement": "mA", "device_class": "current", "state_class": "measurement",
            "precision": 2, "entity_category": "diagnostic"}
_FAULT = {"state_class": "measurement", "icon": "mdi:alert-circle-outline", "precision": 0,
          "entity_category": "diagnostic"}
_VOLTAGE = {"unit_of_measurement": "V", "device_class": "voltage", "state_class": "measurement", "precision": 3}


def parse_loop(value: Any) -> Optional[str]:
    if value in (None, False, "", "none", "voltage"):
        return None
    if str(value).strip().lower().replace(" ", "") in _LOOP_SPELLINGS:
        return LOOP_4_20
    raise ValueError(f"ads1115 loop {value!r}: only '4-20mA' is supported")


def parse_alpha(raw: Any) -> float:
    """``smoothing: 0.2`` or ``smoothing: {alpha: 0.2}``; omitted/false = off."""
    if raw in (None, False, 0):
        return 1.0
    if isinstance(raw, Mapping):
        raw = raw.get("alpha", 1.0)
    return float(raw)


class Ads1115AnalogDriver:
    def __init__(
        self,
        adc: Ads1115,
        channel: Any = 0,
        gain: Any = 1,
        data_rate: Any = 128,
        shunt_ohms: Optional[float] = None,
        loop: Any = None,
        thresholds: Optional[LoopThresholds] = None,
        scale: Optional[Mapping[str, Any]] = None,
        smoothing_alpha: float = 1.0,
        oversample: int = 1,
        clamp: bool = False,
        supply_v: float = 3.3,
    ) -> None:
        self.adc = adc
        self.channel = parse_channel(channel)
        self.gain = parse_gain(gain)
        self.data_rate = parse_data_rate(data_rate)
        self.loop = parse_loop(loop)
        self.thresholds = thresholds or LoopThresholds()
        self.oversample = int(oversample)
        if not 1 <= self.oversample <= 64:
            raise ValueError("ads1115 oversample must be 1-64")
        self.clamp = bool(clamp)
        self.supply_v = float(supply_v)
        self._ema = Ema(smoothing_alpha)
        self.fsr = full_scale_volts(self.gain)
        if self.loop:
            if shunt_ohms is None:
                raise ValueError("ads1115 loop: 4-20mA needs shunt_ohms (e.g. 150)")
            self.shunt_ohms = float(shunt_ohms)
            if self.shunt_ohms <= 0:
                raise ValueError("ads1115 shunt_ohms must be > 0")
            self.scale = LinearScale.from_config(scale, 4.0, 20.0, 0.0, 100.0)
            self._check_headroom()
            self.default_readings: Dict[str, Dict[str, Any]] = {
                "value": {"state_class": "measurement", "precision": 1},
                "current": dict(_CURRENT),
                "loop_fault": dict(_FAULT),
            }
        else:
            self.shunt_ohms = float(shunt_ohms) if shunt_ohms is not None else None
            self.scale = LinearScale.from_config(scale, 0.0, min(self.fsr, self.supply_v), 0.0,
                                                 min(self.fsr, self.supply_v)) if scale else None
            self.default_readings = {"value": dict(_VOLTAGE), "voltage": dict(_VOLTAGE)}

    def _check_headroom(self) -> None:
        """Warn when the fault-high current cannot be seen: the shunt voltage must
        stay inside the PGA full scale and below VDD (the datasheet's VDD + 0.3 V
        absolute limit is a damage limit, not a measuring range)."""
        v_high = ma_to_volts(self.thresholds.fault_high_ma, self.shunt_ohms)
        limit = min(self.fsr, self.supply_v)
        if v_high > limit:
            log.warning(
                "ads1115 ch %s: %.1f mA x %.0f ohm = %.3f V exceeds the measurable %.3f V "
                "(gain %s, VDD %.2f V): over-range faults may read as a saturated value",
                self.channel, self.thresholds.fault_high_ma, self.shunt_ohms, v_high, limit, self.gain, self.supply_v)

    @classmethod
    def from_config(cls, spec: Dict[str, Any], bus: Optional[BusRef] = None, sleep: Sleep = time.sleep
                    ) -> "Ads1115AnalogDriver":
        address = parse_address(spec.get("address"), 0x48)
        adc = Ads1115(bus if bus is not None else int(spec.get("bus", 1)), address, sleep=sleep)
        ha = spec.get("ha") or {}
        return cls(
            adc,
            channel=spec.get("channel", 0),
            gain=spec.get("gain", 1),
            data_rate=spec.get("data_rate", 128),
            shunt_ohms=spec.get("shunt_ohms"),
            loop=spec.get("loop"),
            thresholds=LoopThresholds.from_config(spec.get("fault")),
            scale=spec.get("scale", ha.get("scale") if isinstance(ha, Mapping) else None),
            smoothing_alpha=parse_alpha(spec.get("smoothing")),
            oversample=spec.get("oversample", 1),
            clamp=spec.get("clamp", False),
            supply_v=spec.get("supply_v", 3.3),
        )

    # ------------------------------------------------------------------ read
    def read_counts(self) -> float:
        codes = [self.adc.read_raw(self.channel, self.gain, self.data_rate) for _ in range(self.oversample)]
        return sum(codes) / len(codes)

    def read(self) -> LoopReading:
        code = self.read_counts()
        volts = counts_to_volts(code, self.gain)
        saturated = code >= CODE_MAX or code <= CODE_MIN
        if self.loop:
            return self._loop_reading(code, volts, saturated)
        smoothed = self._ema.update(volts)
        value = self.scale.apply(smoothed) if self.scale else smoothed
        attrs = {"raw": int(round(code)), "voltage": round(volts, 5)}
        return LoopReading({"value": value, "voltage": volts},
                           quality=QUALITY_UNCERTAIN if saturated else QUALITY_GOOD, attributes=attrs)

    def _loop_reading(self, code: float, volts: float, saturated: bool) -> LoopReading:
        ma = volts_to_ma(volts, self.shunt_ohms)
        status = self.thresholds.classify(ma)
        if saturated and status not in FAULTS:
            # The ADC is pinned at full scale: the true current is unknown and at
            # least this high. Treat as a high fault rather than a valid value.
            status = STATUS_FAULT_HIGH
        attrs: Dict[str, Any] = {
            "loop_status": status,
            "current_ma": round(ma, 4),
            "voltage": round(volts, 5),
            "raw": int(round(code)),
        }
        if status in FAULTS:
            self._ema.reset()
            value: Optional[float] = None
        else:
            smoothed = self._ema.update(ma)
            if self.clamp:
                smoothed = min(20.0, max(4.0, smoothed))
            value = self.scale.apply(smoothed)
            if not math.isfinite(value):
                value = None
        quality = QUALITY_GOOD if status == STATUS_OK else QUALITY_UNCERTAIN
        return LoopReading(
            {"value": value, "current": ma, "loop_fault": float(STATUS_CODES[status])},
            quality=quality,
            attributes=attrs,
        )
