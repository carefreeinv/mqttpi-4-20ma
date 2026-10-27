"""mqttpi-4-20ma: ADS1115 analog inputs and 4-20 mA current loops for mqttpi."""

from __future__ import annotations

__version__ = "0.1.0"

DRIVER_TYPES = ("ads1115_loop", "ads1115")


def register(replace: bool = False) -> None:
    """Register the driver with mqttpi's sensor registry.

    ``ads1115_loop`` is always (re)registered. ``ads1115`` is registered too
    unless mqttpi already ships a driver under that name (pass ``replace=True``
    to override it)."""
    from mqttpi.sensors.registry import available_drivers, register_driver

    from mqttpi_4_20ma.driver import Ads1115AnalogDriver

    register_driver("ads1115_loop", Ads1115AnalogDriver.from_config, replace=True)
    if replace or "ads1115" not in available_drivers():
        register_driver("ads1115", Ads1115AnalogDriver.from_config, replace=True)
