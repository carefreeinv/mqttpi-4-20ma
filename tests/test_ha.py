"""HA discovery payloads and fault-aware availability through mqttpi's sensor subsystem."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mqttpi.config import load_config
from mqttpi.sensors.config import parse_sensors
from mqttpi.sensors.scheduler import ScheduledSensor

import mqttpi.daemon as mqttpi_daemon

from mqttpi_4_20ma.config import pins_to_sensors
from mqttpi_4_20ma.daemon import install
from mqttpi_4_20ma.ha import FaultAwareSensorSubsystem
from tests.fakes import NOSLEEP, FakeAds1115Bus, FakeConnection

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = Path(__file__).parent / "snapshots"
BASE = "mqttpi/gas-01/sensors"


def check(name: str, payloads: dict) -> None:
    path = SNAPSHOTS / f"{name}.json"
    text = json.dumps(payloads, indent=2, sort_keys=True) + "\n"
    if os.environ.get("MQTTPI_UPDATE_SNAPSHOTS") or not path.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text)
    assert json.loads(path.read_text()) == json.loads(text), (
        f"discovery payloads changed; if intended, regenerate {path.name} "
        "with MQTTPI_UPDATE_SNAPSHOTS=1 and review the diff")


@pytest.fixture
def subsystem(registered):
    bus = FakeAds1115Bus()
    cfg = pins_to_sensors(load_config(ROOT / "examples" / "gas-detector-4-20ma.yaml"))
    pairs = parse_sensors(cfg)
    for _sc, drv in pairs:
        drv.adc._bus_ref = bus
        drv.adc._sleep = NOSLEEP
    conn = FakeConnection()
    sub = FaultAwareSensorSubsystem(cfg, conn, sensors=pairs, clock=lambda: 0.0)
    return sub, conn, bus


def read(sub, name: str):
    sc, drv = next((sc, d) for sc, d in sub.sensors if sc.name == name)
    return ScheduledSensor(name, drv, sc.interval_s, sc.timeout_s, sc.stale_after_s), drv.read()


def test_discovery_snapshot(subsystem) -> None:
    sub, conn, _ = subsystem
    sub.publisher.publish_discovery(sub.entities)
    payloads = {p.topic: json.loads(p.payload) for p in conn.published if p.topic.startswith("homeassistant/")}
    assert set(payloads) == {f"homeassistant/sensor/gas-01_{a}/config" for a in (
        "gas_lel", "gas_lel_current", "gas_lel_loop_fault", "gas_h2s", "gas_h2s_current", "gas_h2s_loop_fault")}
    lel = payloads["homeassistant/sensor/gas-01_gas_lel/config"]
    assert lel["state_topic"] == f"{BASE}/gas_lel/state"
    assert lel["availability"][1] == {"topic": f"{BASE}/gas_lel/availability"}
    assert lel["unit_of_measurement"] == "%LEL" and "device_class" not in lel
    assert payloads["homeassistant/sensor/gas-01_gas_lel_current/config"]["entity_category"] == "diagnostic"
    check("gas_discovery", payloads)


def test_good_reading_publishes_state_and_attributes(subsystem) -> None:
    sub, conn, bus = subsystem
    bus.set_ma(0x48, 0, 12.0)
    s, r = read(sub, "gas_lel")
    sub._on_reading(s, r)
    assert conn.last(f"{BASE}/gas_lel/state") == "50.0"
    assert conn.last(f"{BASE}/gas_lel_current/state") == "12.00"
    assert conn.last(f"{BASE}/gas_lel_loop_fault/state") == "0"
    assert conn.last(f"{BASE}/gas_lel/availability") == "online"
    attrs = json.loads(conn.last(f"{BASE}/gas_lel/attributes"))
    assert attrs["loop_status"] == "ok" and attrs["quality"] == "good"
    assert attrs["current_ma"] == pytest.approx(12.0, abs=0.001) and "timestamp" in attrs


def test_open_loop_marks_value_unavailable_and_recovers(subsystem) -> None:
    sub, conn, bus = subsystem
    bus.set_ma(0x48, 0, 12.0)
    sub._on_reading(*read(sub, "gas_lel"))
    n_states = sum(1 for p in conn.published if p.topic == f"{BASE}/gas_lel/state")

    bus.set_ma(0x48, 0, 0.0)  # wire cut
    sub._on_reading(*read(sub, "gas_lel"))
    assert conn.last(f"{BASE}/gas_lel/availability") == "offline"
    assert sum(1 for p in conn.published if p.topic == f"{BASE}/gas_lel/state") == n_states  # no stale value
    assert conn.last(f"{BASE}/gas_lel_current/availability") == "online"
    assert conn.last(f"{BASE}/gas_lel_loop_fault/state") == "3"
    assert json.loads(conn.last(f"{BASE}/gas_lel_current/attributes"))["loop_status"] == "fault_low"

    sub.announce()  # reconnect while faulted: still offline
    assert conn.last(f"{BASE}/gas_lel/availability") == "offline"
    assert conn.last(f"{BASE}/gas_lel_current/availability") == "online"

    bus.set_ma(0x48, 0, 8.0)
    sub._on_reading(*read(sub, "gas_lel"))
    assert conn.last(f"{BASE}/gas_lel/availability") == "online"
    assert conn.last(f"{BASE}/gas_lel/state") == "25.0"


def test_stale_sensor_takes_all_entities_offline(subsystem) -> None:
    sub, conn, bus = subsystem
    bus.set_ma(0x48, 1, 4.0)
    s, r = read(sub, "gas_h2s")
    sub._on_reading(s, r)
    sub._on_stale(s)
    for alias in ("gas_h2s", "gas_h2s_current", "gas_h2s_loop_fault"):
        assert conn.last(f"{BASE}/{alias}/availability") == "offline"


def test_install_swaps_daemon_subsystem(monkeypatch) -> None:
    monkeypatch.setattr(mqttpi_daemon, "SensorSubsystem", mqttpi_daemon.SensorSubsystem)
    install()
    assert mqttpi_daemon.SensorSubsystem is FaultAwareSensorSubsystem


def test_cli_missing_config(tmp_path) -> None:
    from mqttpi_4_20ma.daemon import main
    assert main(["-c", str(tmp_path / "nope.yaml")]) == 1
