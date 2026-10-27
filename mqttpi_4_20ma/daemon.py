"""``mqttpi-4-20ma``: run the stock mqttpi daemon with this package's driver.

Same command-line options as ``mqttpi``. Before starting it:

1. registers the ``ads1115_loop`` / ``ads1115`` sensor driver;
2. maps ``pins[]`` entries with ``source: ads1115`` onto ``sensors``;
3. uses :class:`~mqttpi_4_20ma.ha.FaultAwareSensorSubsystem`, so a loop fault
   marks the engineering-value entity unavailable instead of freezing it.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import mqttpi.daemon as mqttpi_daemon
from mqttpi.config import load_config

from mqttpi_4_20ma import __version__, register
from mqttpi_4_20ma.config import pins_to_sensors
from mqttpi_4_20ma.ha import FaultAwareSensorSubsystem

log = logging.getLogger("mqttpi_4_20ma")


def install() -> None:
    """Register the driver and swap in the fault-aware sensor subsystem."""
    register()
    mqttpi_daemon.SensorSubsystem = FaultAwareSensorSubsystem


def prepare_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return pins_to_sensors(cfg)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="mqttpi daemon with ADS1115 / 4-20 mA current-loop inputs")
    parser.add_argument("-c", "--config", type=Path, default=Path("config.yaml"), help="Path to config.yaml")
    parser.add_argument("-s", "--secrets", type=Path, default=Path("secrets.yaml"),
                        help="Path to secrets.yaml (optional)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--mock-gpio", action="store_true", help="Use in-memory GPIO backend (no hardware)")
    parser.add_argument("--version", action="version", version=f"mqttpi-4-20ma {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if not args.config.exists():
        log.error("Config not found: %s", args.config)
        return 1
    try:
        install()
        cfg = prepare_config(load_config(args.config, args.secrets if args.secrets.exists() else None))
        mqttpi_daemon.Daemon(cfg, mock_gpio=args.mock_gpio).run()
    except (RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
