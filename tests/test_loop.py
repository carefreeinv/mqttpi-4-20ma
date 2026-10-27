"""Current-loop math: volts -> mA -> engineering units, NE43 fault limits, smoothing."""

from __future__ import annotations

import pytest

from mqttpi_4_20ma.loop import (
    STATUS_FAULT_HIGH,
    STATUS_FAULT_LOW,
    STATUS_OK,
    STATUS_OVER_RANGE,
    STATUS_UNDER_RANGE,
    Ema,
    LinearScale,
    LoopThresholds,
    ma_to_volts,
    volts_to_ma,
)


def test_shunt_ohms_law() -> None:
    assert volts_to_ma(3.0, 150) == pytest.approx(20.0)
    assert volts_to_ma(0.6, 150) == pytest.approx(4.0)
    assert ma_to_volts(12.0, 150) == pytest.approx(1.8)
    with pytest.raises(ValueError):
        volts_to_ma(1.0, 0)


@pytest.mark.parametrize("ma,lel", [(4.0, 0.0), (8.0, 25.0), (12.0, 50.0), (20.0, 100.0)])
def test_linear_scale_4_20_to_lel(ma, lel) -> None:
    scale = LinearScale.from_config({"min": 0, "max": 100}, 4.0, 20.0, 0.0, 100.0)
    assert scale.apply(ma) == pytest.approx(lel)


def test_linear_scale_custom_and_reversed() -> None:
    assert LinearScale.from_config({"min": -40, "max": 85}, 4, 20, 0, 100).apply(12) == pytest.approx(22.5)
    assert LinearScale(100, 0, 4, 20).apply(16) == pytest.approx(25)
    with pytest.raises(ValueError):
        LinearScale(0, 1, 4, 4)


@pytest.mark.parametrize("ma,status", [
    (0.0, STATUS_FAULT_LOW),      # open loop / broken wire
    (3.5, STATUS_FAULT_LOW),
    (3.6, STATUS_FAULT_LOW),      # NE43: <= 3.6 mA is a failure signal
    (3.7, STATUS_UNDER_RANGE),
    (3.9, STATUS_UNDER_RANGE),
    (4.0, STATUS_OK),
    (12.0, STATUS_OK),
    (20.0, STATUS_OK),
    (20.3, STATUS_OVER_RANGE),
    (20.9, STATUS_OVER_RANGE),
    (21.0, STATUS_FAULT_HIGH),    # NE43: >= 21 mA is a failure signal
    (22.0, STATUS_FAULT_HIGH),
])
def test_ne43_classification(ma, status) -> None:
    assert LoopThresholds().classify(ma) == status


def test_thresholds_configurable_and_validated() -> None:
    th = LoopThresholds.from_config({"low_ma": 3.8, "high_ma": 20.5})
    assert th.classify(3.7) == STATUS_FAULT_LOW and th.classify(20.6) == STATUS_FAULT_HIGH
    for bad in ({"low_ma": 4.5}, {"high_ma": 19}, {"low_ma": -1}):
        with pytest.raises(ValueError):
            LoopThresholds.from_config(bad)


def test_ema() -> None:
    ema = Ema(0.5)
    assert ema.update(10) == 10
    assert ema.update(20) == 15
    ema.reset()
    assert ema.update(4) == 4
    assert Ema().update(7) == 7  # alpha 1 = passthrough
    with pytest.raises(ValueError):
        Ema(0)
