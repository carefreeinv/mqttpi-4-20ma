"""TI ADS1115 16-bit I2C ADC: register encoding and single-shot reads.

Register map, bit fields and limits follow the TI datasheet (SBAS444E):

* pointer ``0x00`` = Conversion register (16-bit two's complement, MSB first),
  pointer ``0x01`` = Config register;
* Config: ``OS[15] MUX[14:12] PGA[11:9] MODE[8] DR[7:5] COMP_MODE[4]
  COMP_POL[3] COMP_LAT[2] COMP_QUE[1:0]``;
* 1 LSB = FSR / 32768; conversion time = 1 / DR (data rate tolerance ±10 %);
* no more than VDD + 0.3 V may be applied to any analog input, whatever the
  PGA full-scale range says.

Hardware access goes through mqttpi's ``I2cBus`` protocol, so tests inject a
fake bus and production code uses mqttpi's shared, lazily-opened ``smbus2`` bus.
"""

from __future__ import annotations

import threading
import time
from fractions import Fraction
from typing import Any, Callable, Dict, Tuple, Union

from mqttpi.sensors.i2c import I2cBus, open_bus

REG_CONVERSION = 0x00
REG_CONFIG = 0x01

#: ADDR pin to GND / VDD / SDA / SCL.
ADDRESSES = (0x48, 0x49, 0x4A, 0x4B)

#: config ``gain`` -> (PGA[2:0] bits, full-scale range in volts).
PGA: Dict[str, Tuple[int, float]] = {
    "2/3": (0b000, 6.144),
    "1": (0b001, 4.096),
    "2": (0b010, 2.048),
    "4": (0b011, 1.024),
    "8": (0b100, 0.512),
    "16": (0b101, 0.256),
}

#: samples per second -> DR[2:0] bits.
DATA_RATES: Dict[int, int] = {8: 0b000, 16: 0b001, 32: 0b010, 64: 0b011,
                              128: 0b100, 250: 0b101, 475: 0b110, 860: 0b111}

#: config ``channel`` -> MUX[2:0] bits. Single-ended AINx vs GND, or differential AINp-AINn.
MUX: Dict[str, int] = {
    "0": 0b100, "1": 0b101, "2": 0b110, "3": 0b111,
    "0-1": 0b000, "0-3": 0b001, "1-3": 0b010, "2-3": 0b011,
}

FULL_SCALE_CODE = 32768
CODE_MAX = 32767
CODE_MIN = -32768

Sleep = Callable[[float], None]
BusRef = Union[I2cBus, int]


def parse_gain(value: Any) -> str:
    """Accept ``2/3`` (YAML gives the string ``"2/3"``), ``0.667``, ``1``, ``"16"`` ..."""
    if value is None:
        return "1"
    text = str(value).strip().lower().replace(" ", "")
    if text in PGA:
        return text
    try:
        frac = Fraction(text).limit_denominator(3)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"ads1115 gain {value!r}: use one of 2/3, 1, 2, 4, 8, 16") from None
    key = "2/3" if frac == Fraction(2, 3) else (str(frac.numerator) if frac.denominator == 1 else "")
    if key not in PGA:
        raise ValueError(f"ads1115 gain {value!r}: use one of 2/3, 1, 2, 4, 8, 16")
    return key


def full_scale_volts(gain: Any) -> float:
    return PGA[parse_gain(gain)][1]


def parse_channel(value: Any) -> str:
    """``0``-``3`` / ``"A0"``-``"A3"`` / ``"AIN2"`` single-ended, or ``"0-1"``,
    ``"0-3"``, ``"1-3"``, ``"2-3"`` differential (the pairs the MUX supports)."""
    if value is None:
        return "0"
    text = str(value).strip().upper().replace("AIN", "").replace("A", "").replace(" ", "")
    if text not in MUX:
        raise ValueError(f"ads1115 channel {value!r}: use 0-3 (A0-A3) or one of \"0-1\", \"0-3\", \"1-3\", \"2-3\"")
    return text


def parse_data_rate(value: Any) -> int:
    rate = 128 if value is None else int(value)
    if rate not in DATA_RATES:
        raise ValueError(f"ads1115 data_rate {value!r}: use one of {sorted(DATA_RATES)}")
    return rate


def config_word(channel: str, gain: str, data_rate: int) -> int:
    """Single-shot conversion start, comparator disabled (COMP_QUE = 11)."""
    return (
        (1 << 15)  # OS: start a single conversion
        | (MUX[channel] << 12)
        | (PGA[gain][0] << 9)
        | (1 << 8)  # MODE: single-shot / power-down
        | (DATA_RATES[data_rate] << 5)
        | 0b00011
    )


def signed16(msb: int, lsb: int) -> int:
    code = (msb << 8) | lsb
    return code - 0x10000 if code & 0x8000 else code


def counts_to_volts(code: float, gain: Any) -> float:
    return code * full_scale_volts(gain) / FULL_SCALE_CODE


def volts_to_counts(volts: float, gain: Any) -> int:
    """Inverse of :func:`counts_to_volts`, saturated like the ADC (test helper)."""
    code = int(round(volts * FULL_SCALE_CODE / full_scale_volts(gain)))
    return max(CODE_MIN, min(CODE_MAX, code))


# One lock per (bus, address): the mqttpi scheduler reads every sensor in its
# own worker thread, and a chip shared by several channels must finish one
# config-write / wait / read cycle before the next channel reprograms the MUX.
_chip_locks: Dict[Tuple[Any, int], threading.Lock] = {}
_chip_locks_guard = threading.Lock()


def _chip_lock(bus_key: Any, address: int) -> threading.Lock:
    with _chip_locks_guard:
        return _chip_locks.setdefault((bus_key, address), threading.Lock())


class Ads1115:
    """One ADS1115 chip. ``bus`` is an ``I2cBus`` or a bus number opened lazily
    on first use, so a missing ``smbus2`` or ``/dev/i2c-N`` marks the sensor
    unavailable instead of stopping the daemon."""

    POLL_S = 0.001
    MAX_POLLS = 50

    def __init__(self, bus: BusRef = 1, address: int = 0x48, sleep: Sleep = time.sleep) -> None:
        if address not in ADDRESSES:
            raise ValueError(f"ads1115 address 0x{address:02x}: use 0x48-0x4B")
        self._bus_ref = bus
        self.address = address
        self._sleep = sleep
        self._lock = _chip_lock(bus if isinstance(bus, int) else id(bus), address)

    @property
    def bus(self) -> I2cBus:
        if isinstance(self._bus_ref, int):
            self._bus_ref = open_bus(self._bus_ref)
        return self._bus_ref

    def read_raw(self, channel: str = "0", gain: str = "1", data_rate: int = 128) -> int:
        """Start one single-shot conversion and return the signed 16-bit code."""
        cfg = config_word(channel, gain, data_rate)
        with self._lock:
            self.bus.write(self.address, [REG_CONFIG, (cfg >> 8) & 0xFF, cfg & 0xFF])
            # Conversion time is 1/DR with a ±10 % data-rate tolerance.
            self._sleep(1.1 / data_rate + 0.0005)
            for _ in range(self.MAX_POLLS):
                status = self.bus.write_read(self.address, [REG_CONFIG], 2)
                if status[0] & 0x80:  # OS reads 1: not converting
                    break
                self._sleep(self.POLL_S)
            else:
                raise IOError(f"ADS1115 @0x{self.address:02x}: conversion did not complete")
            data = self.bus.write_read(self.address, [REG_CONVERSION], 2)
        if len(data) != 2:
            raise IOError(f"ADS1115 @0x{self.address:02x}: short read ({len(data)} bytes)")
        return signed16(data[0], data[1])
