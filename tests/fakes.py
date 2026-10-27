"""Hardware-free stand-ins: a fake ADS1115 on a fake I2C bus, and a fake MQTT connection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from mqttpi_4_20ma.ads1115 import MUX, PGA, REG_CONFIG, REG_CONVERSION, volts_to_counts

_MUX_INPUTS = {bits: key for key, bits in MUX.items()}
_PGA_GAIN = {bits: key for key, (bits, _fsr) in PGA.items()}
_PGA_GAIN.update({0b110: "16", 0b111: "16"})


class FakeAds1115Bus:
    """Emulates the ADS1115 register interface for any of 0x48-0x4B.

    ``inputs[address][n]`` is the voltage on AINn (or a callable returning it).
    A config write with OS=1 performs the conversion; ``busy_polls`` makes the
    OS bit read 0 ("converting") that many times first."""

    def __init__(self) -> None:
        self.inputs: Dict[int, Dict[int, object]] = {}
        self.config: Dict[int, int] = {}
        self.conversion: Dict[int, int] = {}
        self.writes: List[Tuple[int, Tuple[int, ...]]] = []
        self.busy_polls = 0
        self._busy: Dict[int, int] = {}
        self.fail: Optional[BaseException] = None

    def set_volts(self, address: int, channel: int, volts) -> None:
        self.inputs.setdefault(address, {})[channel] = volts

    def set_ma(self, address: int, channel: int, ma: float, shunt_ohms: float = 150.0) -> None:
        self.set_volts(address, channel, ma * shunt_ohms / 1000.0)

    def _vin(self, address: int, ain: int) -> float:
        v = self.inputs.get(address, {}).get(ain, 0.0)
        return float(v() if callable(v) else v)

    def write(self, address: int, data: Sequence[int]) -> None:
        if self.fail:
            raise self.fail
        data = tuple(data)
        self.writes.append((address, data))
        if data[0] == REG_CONFIG and len(data) == 3:
            word = (data[1] << 8) | data[2]
            self.config[address] = word
            mux = _MUX_INPUTS[(word >> 12) & 0b111]
            gain = _PGA_GAIN[(word >> 9) & 0b111]
            if "-" in mux:
                p, n = (int(x) for x in mux.split("-"))
                vin = self._vin(address, p) - self._vin(address, n)
            else:
                vin = self._vin(address, int(mux))
            self.conversion[address] = volts_to_counts(vin, gain) & 0xFFFF
            self._busy[address] = self.busy_polls

    def read(self, address: int, length: int) -> bytes:  # pragma: no cover - unused by the driver
        raise NotImplementedError

    def write_read(self, address: int, data: Sequence[int], length: int) -> bytes:
        if self.fail:
            raise self.fail
        reg = data[0]
        if reg == REG_CONFIG:
            word = self.config.get(address, 0x8583)
            if self._busy.get(address, 0) > 0:
                self._busy[address] -= 1
                word &= 0x7FFF
            else:
                word |= 0x8000
            return bytes([(word >> 8) & 0xFF, word & 0xFF])
        if reg == REG_CONVERSION:
            code = self.conversion.get(address, 0)
            return bytes([(code >> 8) & 0xFF, code & 0xFF])
        raise IOError(f"unexpected register 0x{reg:02x}")


@dataclass
class Published:
    topic: str
    payload: str
    retain: Optional[bool]


@dataclass
class FakeConnection:
    """The slice of mqttpi's ``MqttConnection`` used by the sensor subsystem."""

    base_topic: str = "mqttpi/gas-01"
    availability_topic: str = "mqttpi/gas-01/status"
    payload_available: str = "online"
    payload_not_available: str = "offline"
    is_connected: bool = True
    published: List[Published] = field(default_factory=list)
    callbacks: List[Callable[[], None]] = field(default_factory=list)

    def publish(self, topic: str, payload, retain=None) -> None:
        self.published.append(Published(topic, str(payload), retain))

    def add_connect_callback(self, cb: Callable[[], None]) -> None:
        self.callbacks.append(cb)

    def last(self, topic: str) -> Optional[str]:
        for p in reversed(self.published):
            if p.topic == topic:
                return p.payload
        return None


NOSLEEP = lambda s: None  # noqa: E731
