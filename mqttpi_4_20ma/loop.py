"""4-20 mA current-loop math: volts -> mA -> engineering units, NAMUR NE43 style
status, linear scaling and smoothing. Pure functions, no I/O.

Default thresholds follow the NAMUR NE43 signal levels:

* 4-20 mA: normal measurement (``ok``);
* between the fault limit and 4 mA, or between 20 mA and the fault limit:
  ``under_range`` / ``over_range``. NE43 calls 3.8-4 and 20-20.5 mA valid
  under/over range; the value is still published, flagged ``uncertain``;
* <= 3.6 mA or >= 21.0 mA (configurable): failure signal (open loop, broken
  wire, transmitter fault, short). The engineering value is withheld and its
  Home Assistant entity goes unavailable.

Many gas transmitters also drive a sub-4 mA level for maintenance, calibration
or fault states; check the transmitter manual and adjust the thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

STATUS_OK = "ok"
STATUS_UNDER_RANGE = "under_range"
STATUS_OVER_RANGE = "over_range"
STATUS_FAULT_LOW = "fault_low"  # open loop / broken wire / transmitter fault (<= 3.6 mA)
STATUS_FAULT_HIGH = "fault_high"  # short / transmitter fault (>= 21 mA)

#: Numeric code published as the ``loop_fault`` reading (HA-friendly).
STATUS_CODES = {
    STATUS_OK: 0,
    STATUS_UNDER_RANGE: 1,
    STATUS_OVER_RANGE: 2,
    STATUS_FAULT_LOW: 3,
    STATUS_FAULT_HIGH: 4,
}
FAULTS = (STATUS_FAULT_LOW, STATUS_FAULT_HIGH)


@dataclass(frozen=True)
class LoopThresholds:
    """Failure-signal limits. ``fault_low_ma`` <= reading -> open loop / fault;
    reading >= ``fault_high_ma`` -> over-range fault. NE43 defaults: 3.6 / 21.0.
    Use e.g. 3.8 / 20.5 to treat anything outside the NE43 measurement band as a fault."""

    fault_low_ma: float = 3.6
    fault_high_ma: float = 21.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.fault_low_ma <= 4.0 and 20.0 <= self.fault_high_ma):
            raise ValueError(
                "loop fault thresholds must satisfy 0 <= low_ma <= 4.0 and high_ma >= 20.0 "
                f"(got low_ma={self.fault_low_ma}, high_ma={self.fault_high_ma})")

    @classmethod
    def from_config(cls, raw: Optional[Mapping[str, Any]]) -> "LoopThresholds":
        raw = raw or {}
        d = cls()
        return cls(
            fault_low_ma=float(raw.get("low_ma", d.fault_low_ma)),
            fault_high_ma=float(raw.get("high_ma", d.fault_high_ma)),
        )

    def classify(self, ma: float) -> str:
        if ma <= self.fault_low_ma:
            return STATUS_FAULT_LOW
        if ma >= self.fault_high_ma:
            return STATUS_FAULT_HIGH
        if ma < 4.0:
            return STATUS_UNDER_RANGE
        if ma > 20.0:
            return STATUS_OVER_RANGE
        return STATUS_OK


def volts_to_ma(volts: float, shunt_ohms: float) -> float:
    """Ohm's law across the shunt: I[mA] = V / R * 1000."""
    if shunt_ohms <= 0:
        raise ValueError("shunt_ohms must be > 0")
    return volts / shunt_ohms * 1000.0


def ma_to_volts(ma: float, shunt_ohms: float) -> float:
    return ma * shunt_ohms / 1000.0


@dataclass(frozen=True)
class LinearScale:
    """Map ``in_min..in_max`` linearly onto ``min..max`` (``ha.scale`` in mqttpi's schema).

    For a 4-20 mA loop the input is in mA (default 4..20), so ``min`` is the
    value at 4 mA (range_min) and ``max`` the value at 20 mA (range_max)."""

    out_min: float
    out_max: float
    in_min: float
    in_max: float

    def __post_init__(self) -> None:
        if self.in_max == self.in_min:
            raise ValueError("scale: in_min and in_max must differ")

    def apply(self, x: float) -> float:
        return self.out_min + (x - self.in_min) * (self.out_max - self.out_min) / (self.in_max - self.in_min)

    @classmethod
    def from_config(cls, raw: Optional[Mapping[str, Any]], in_min: float, in_max: float,
                    out_min: float, out_max: float) -> "LinearScale":
        raw = raw or {}
        return cls(
            out_min=float(raw.get("min", out_min)),
            out_max=float(raw.get("max", out_max)),
            in_min=float(raw.get("in_min", in_min)),
            in_max=float(raw.get("in_max", in_max)),
        )


class Ema:
    """Exponential moving average. ``alpha`` = 1 disables smoothing."""

    def __init__(self, alpha: float = 1.0) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("smoothing alpha must be in (0, 1]")
        self.alpha = alpha
        self.value: Optional[float] = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.value + self.alpha * (x - self.value)
        return self.value

    def reset(self) -> None:
        self.value = None
