"""Config: mqttpi's pins[] analog_input schema -> sensors, parsed by mqttpi itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from mqttpi.config import load_config
from mqttpi.daemon import enabled_subsystems
from mqttpi.sensors.config import parse_sensors, sensors_configured
from mqttpi.sensors.registry import available_drivers

from mqttpi_4_20ma.config import DRIVER_TYPE, pin_to_sensor, pins_to_sensors
from mqttpi_4_20ma.driver import Ads1115AnalogDriver

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "gas-detector-4-20ma.yaml"


def test_register_adds_driver_types(registered) -> None:
    assert {"ads1115", "ads1115_loop"} <= set(available_drivers())


def test_example_maps_pins_to_sensors(registered) -> None:
    raw = load_config(EXAMPLE)
    cfg = pins_to_sensors(raw)
    assert raw["pins"] and len(raw["pins"]) == 2  # input not mutated
    assert cfg["pins"] == []
    assert set(cfg["sensors"]) == {"gas_lel", "gas_h2s"}
    lel = cfg["sensors"]["gas_lel"]
    assert lel["type"] == DRIVER_TYPE
    assert lel["shunt_ohms"] == 150 and lel["loop"] == "4-20mA" and lel["channel"] == 0
    assert lel["scale"] == {"min": 0.0, "max": 100.0, "in_min": 4.0, "in_max": 20.0}
    assert sensors_configured(cfg)
    assert enabled_subsystems(cfg) == ["sensors"]


def test_example_parses_with_mqttpi(registered) -> None:
    cfg = pins_to_sensors(load_config(EXAMPLE))
    pairs = parse_sensors(cfg)
    assert [sc.name for sc, _ in pairs] == ["gas_lel", "gas_h2s"]
    sc, drv = pairs[0]
    assert isinstance(drv, Ads1115AnalogDriver)
    assert drv.channel == "0" and drv.gain == "1" and drv.data_rate == 128 and drv.oversample == 4
    assert drv.thresholds.fault_low_ma == 3.6 and drv.thresholds.fault_high_ma == 21.0
    assert sc.interval_s == 2.0
    ents = {e.key: e for e in sc.entities}
    assert ents["value"].alias == "gas_lel" and ents["value"].unit == "%LEL"
    assert ents["value"].name == "Combustible Gas" and ents["value"].precision == 1
    assert ents["current"].alias == "gas_lel_current" and ents["current"].unit == "mA"
    assert ents["current"].device_class == "current"
    assert ents["current"].extra == {"entity_category": "diagnostic"}
    assert ents["loop_fault"].alias == "gas_lel_loop_fault"
    assert pairs[1][1].channel == "1"


def test_mqttpi_documented_voltage_entry(registered) -> None:
    """The schema-only entry from mqttpi's examples/analog-inputs.yaml loads unchanged."""
    pin = {
        "alias": "supply_voltage", "direction": "analog_input", "source": "ads1115", "bus": 1,
        "address": 0x48, "channel": 0, "gain": 1, "data_rate": 128, "publish": {"interval_ms": 5000},
        "ha": {"name": "Supply Voltage", "device_class": "voltage", "state_class": "measurement",
               "unit_of_measurement": "V", "scale": {"min": 0.0, "max": 13.2, "in_min": 0.0, "in_max": 3.3}},
    }
    cfg = pins_to_sensors({"device": {"id": "n"}, "pins": [pin, {"pin": 17, "alias": "relay"}]})
    assert cfg["pins"] == [{"pin": 17, "alias": "relay"}]
    sc, drv = parse_sensors(cfg)[0]
    assert drv.loop is None and drv.scale.out_max == 13.2
    assert {e.alias for e in sc.entities} == {"supply_voltage", "supply_voltage_voltage"}


def test_sensors_section_form(registered) -> None:
    cfg = {"sensors": {"tank": {"type": "ads1115", "channel": 2, "shunt_ohms": 150, "loop": "4-20mA",
                                "scale": {"min": 0, "max": 5000},
                                "readings": {"value": {"alias": "tank_level",
                                                       "ha": {"unit_of_measurement": "L"}}}}}}
    sc, drv = parse_sensors(cfg)[0]
    assert drv.scale.out_max == 5000 and [e.alias for e in sc.entities] == ["tank_level"]


def test_list_sensors_and_collisions() -> None:
    pin = {"alias": "a0", "direction": "analog_input", "source": "ads1115", "shunt_ohms": 150, "loop": "4-20mA"}
    cfg = pins_to_sensors({"pins": [pin], "sensors": [{"name": "cpu", "type": "cpu_temperature"}]})
    assert [s["name"] for s in cfg["sensors"]] == ["cpu", "a0"]
    with pytest.raises(ValueError, match="already exists"):
        pins_to_sensors({"pins": [pin], "sensors": {"a0": {"type": "cpu_temperature"}}})


def test_pin_to_sensor_passthrough() -> None:
    name, spec = pin_to_sensor({"alias": "x", "direction": "analog_input", "source": "ads1115",
                                "ha": {"name": "X", "icon": "mdi:gauge", "precision": 2}})
    assert name == "x"
    assert spec["readings"]["value"] == {"alias": "x", "ha": {"name": "X", "icon": "mdi:gauge"}, "precision": 2}
    assert "voltage" in spec["readings"]


def test_non_ads1115_configs_untouched() -> None:
    cfg = {"pins": [{"pin": 26, "alias": "a", "direction": "analog_input"}]}
    assert pins_to_sensors(cfg) == cfg
