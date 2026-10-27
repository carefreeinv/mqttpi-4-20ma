"""Shared fixtures: a fake ADS1115 bus, registered driver."""

from __future__ import annotations

import pytest

from mqttpi.sensors.i2c import reset_bus_factory, set_bus_factory

from mqttpi_4_20ma import register
from tests.fakes import FakeAds1115Bus


@pytest.fixture
def fake_bus():
    """A fake ADS1115 bus behind mqttpi's shared ``open_bus`` (every bus number)."""
    bus = FakeAds1115Bus()
    set_bus_factory(lambda number: bus)
    yield bus
    reset_bus_factory()


@pytest.fixture
def registered():
    register()
    yield
