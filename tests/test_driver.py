"""Ads1115AnalogDriver: loop mode, voltage mode, faults, smoothing, oversampling."""

from __future__ import annotations

import logging

import pytest

from mqttpi.sensors import QUALITY_GOOD, QUALITY_UNCERTAIN, SensorDriver

from mqttpi_4_20ma.ads1115 import Ads1115
from mqttpi_4_20ma.driver import Ads1115AnalogDriver
from tests.fakes import NOSLEEP, FakeAds1115Bus

LEL = {"type": "ads1115_loop", "address": 0x48, "channel": 0, "gain": 1, "shunt_ohms": 150,
       "loop": "4-20mA", "scale": {"min": 0, "max": 100}}


def loop_driver(bus: FakeAds1115Bus, **overrides) -> Ads1115AnalogDriver:
    return Ads1115AnalogDriver.from_config({**LEL, **overrides}, bus=bus, sleep=NOSLEEP)


def test_is_an_mqttpi_sensor_driver() -> None:
    drv = loop_driver(FakeAds1115Bus())
    assert isinstance(drv, SensorDriver)
    assert set(drv.default_readings) == {"value", "current", "loop_fault"}


@pytest.mark.parametrize("ma,lel", [(4.0, 0.0), (12.0, 50.0), (20.0, 100.0)])
def test_loop_counts_to_engineering_units(ma, lel) -> None:
    bus = FakeAds1115Bus()
    bus.set_ma(0x48, 0, ma)
    r = loop_driver(bus).read()
    assert r.quality == QUALITY_GOOD
    assert r.values["value"] == pytest.approx(lel, abs=0.01)
    assert r.values["current"] == pytest.approx(ma, abs=0.001)
    assert r.values["loop_fault"] == 0
    assert r.attributes["loop_status"] == "ok"


def test_open_loop_withholds_value() -> None:
    bus = FakeAds1115Bus()
    bus.set_ma(0x48, 0, 0.0)  # broken wire: no current through the shunt
    r = loop_driver(bus).read()
    assert r.values["value"] is None
    assert r.values["loop_fault"] == 3
    assert r.quality == QUALITY_UNCERTAIN
    assert r.attributes["loop_status"] == "fault_low"
    assert r.numeric() == {"current": 0.0, "loop_fault": 3.0}


def test_over_range_fault_and_under_range_warning() -> None:
    bus = FakeAds1115Bus()
    drv = loop_driver(bus)
    bus.set_ma(0x48, 0, 21.5)
    r = drv.read()
    assert r.values["value"] is None and r.attributes["loop_status"] == "fault_high" and r.values["loop_fault"] == 4
    bus.set_ma(0x48, 0, 3.8)  # NE43 under range: published, flagged
    r = drv.read()
    assert r.values["value"] == pytest.approx(-1.25, abs=0.01)
    assert r.quality == QUALITY_UNCERTAIN and r.values["loop_fault"] == 1
    bus.set_ma(0x48, 0, 20.4)
    r = drv.read()
    assert r.values["value"] == pytest.approx(102.5, abs=0.01) and r.values["loop_fault"] == 2


def test_clamp_keeps_value_inside_range() -> None:
    bus = FakeAds1115Bus()
    bus.set_ma(0x48, 0, 3.8)
    assert loop_driver(bus, clamp=True).read().values["value"] == pytest.approx(0.0)


def test_saturated_adc_is_a_high_fault() -> None:
    bus = FakeAds1115Bus()
    bus.set_volts(0x48, 0, 5.0)  # beyond full scale: code pins at 32767
    r = loop_driver(bus, shunt_ohms=250, fault={"high_ma": 30}).read()
    assert r.attributes["loop_status"] == "fault_high" and r.values["value"] is None


def test_configurable_thresholds() -> None:
    bus = FakeAds1115Bus()
    bus.set_ma(0x48, 0, 3.7)
    assert loop_driver(bus).read().attributes["loop_status"] == "under_range"
    assert loop_driver(bus, fault={"low_ma": 3.8}).read().attributes["loop_status"] == "fault_low"


def test_smoothing_resets_after_fault() -> None:
    bus = FakeAds1115Bus()
    drv = loop_driver(bus, smoothing={"alpha": 0.5})
    bus.set_ma(0x48, 0, 4.0)
    assert drv.read().values["value"] == pytest.approx(0.0, abs=0.01)
    bus.set_ma(0x48, 0, 20.0)
    assert drv.read().values["value"] == pytest.approx(50.0, abs=0.01)  # EMA halfway
    bus.set_ma(0x48, 0, 0.0)
    assert drv.read().values["value"] is None
    bus.set_ma(0x48, 0, 12.0)
    assert drv.read().values["value"] == pytest.approx(50.0, abs=0.01)  # restarts from the new value
    # loop_fault and current are never smoothed
    bus.set_ma(0x48, 0, 20.0)
    assert drv.read().values["current"] == pytest.approx(20.0, abs=0.001)


def test_oversample_averages_conversions() -> None:
    bus = FakeAds1115Bus()
    seq = iter([0.6, 0.6, 3.0, 3.0])
    bus.set_volts(0x48, 0, lambda: next(seq))
    r = loop_driver(bus, oversample=4).read()
    assert r.values["current"] == pytest.approx(12.0, abs=0.001)
    assert sum(1 for _a, d in bus.writes if d[0] == 0x01) == 4


def test_channel_gain_and_rate_reach_the_chip() -> None:
    bus = FakeAds1115Bus()
    bus.set_ma(0x4B, 3, 12.0, shunt_ohms=100)
    drv = loop_driver(bus, address="0x4B", channel="A3", gain=2, data_rate=860, shunt_ohms=100)
    assert drv.read().values["current"] == pytest.approx(12.0, abs=0.001)
    assert bus.writes[-1] == (0x4B, (0x01, 0xF5, 0xE3))  # MUX=111 PGA=010 MODE=1 DR=111


def test_voltage_mode_matches_mqttpi_schema() -> None:
    """The ``supply_voltage`` entry of mqttpi's examples/analog-inputs.yaml: 1:4 divider."""
    bus = FakeAds1115Bus()
    bus.set_volts(0x48, 0, 3.3)
    spec = {"bus": 1, "address": 0x48, "channel": 0, "gain": 1, "data_rate": 128,
            "ha": {"scale": {"min": 0.0, "max": 13.2, "in_min": 0.0, "in_max": 3.3}}}
    drv = Ads1115AnalogDriver.from_config(spec, bus=bus, sleep=NOSLEEP)
    r = drv.read()
    assert set(drv.default_readings) == {"value", "voltage"}
    assert r.values["value"] == pytest.approx(13.2, abs=0.001)
    assert r.values["voltage"] == pytest.approx(3.3, abs=0.0002)
    assert "current" not in r.values


def test_voltage_mode_without_scale_publishes_volts() -> None:
    bus = FakeAds1115Bus()
    bus.set_volts(0x48, 1, 1.234)
    r = Ads1115AnalogDriver(Ads1115(bus, sleep=NOSLEEP), channel=1).read()
    assert r.values["value"] == pytest.approx(1.234, abs=0.0002)


def test_config_errors() -> None:
    with pytest.raises(ValueError, match="shunt_ohms"):
        Ads1115AnalogDriver.from_config({"loop": "4-20mA"}, bus=FakeAds1115Bus())
    with pytest.raises(ValueError, match="loop"):
        Ads1115AnalogDriver.from_config({"loop": "0-10V", "shunt_ohms": 150}, bus=FakeAds1115Bus())
    with pytest.raises(ValueError, match="oversample"):
        Ads1115AnalogDriver.from_config({**LEL, "oversample": 0}, bus=FakeAds1115Bus())


def test_headroom_warning(caplog) -> None:
    caplog.set_level(logging.WARNING)
    loop_driver(FakeAds1115Bus())  # 21 mA x 150 = 3.15 V < 3.3 V: fine
    assert "exceeds" not in caplog.text
    loop_driver(FakeAds1115Bus(), shunt_ohms=250)  # 21 mA x 250 = 5.25 V > 3.3 V
    assert "exceeds the measurable" in caplog.text


def test_bus_error_propagates_for_the_scheduler() -> None:
    bus = FakeAds1115Bus()
    bus.fail = OSError(121, "Remote I/O error")
    with pytest.raises(OSError):
        loop_driver(bus).read()


def test_lazy_bus_open_via_mqttpi(fake_bus) -> None:
    fake_bus.set_ma(0x48, 0, 12.0)
    drv = Ads1115AnalogDriver.from_config({**LEL, "bus": 1})
    drv.adc._sleep = NOSLEEP
    assert drv.read().values["value"] == pytest.approx(50.0, abs=0.01)
