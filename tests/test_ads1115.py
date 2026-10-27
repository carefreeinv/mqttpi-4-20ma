"""ADS1115 register encoding and single-shot reads against a fake bus."""

from __future__ import annotations

import pytest

from mqttpi_4_20ma.ads1115 import (
    Ads1115,
    config_word,
    counts_to_volts,
    full_scale_volts,
    parse_channel,
    parse_data_rate,
    parse_gain,
    signed16,
    volts_to_counts,
)
from tests.fakes import NOSLEEP, FakeAds1115Bus


def test_config_word_matches_datasheet_fields() -> None:
    # OS=1, MUX=100 (AIN0-GND), PGA=001 (+/-4.096 V), MODE=1, DR=100 (128 SPS), COMP_QUE=11
    assert config_word("0", "1", 128) == 0xC383
    # AIN3-GND, +/-2.048 V, 860 SPS
    assert config_word("3", "2", 860) == 0b1_111_010_1_111_00011
    # differential AIN0-AIN1, +/-6.144 V, 8 SPS
    assert config_word("0-1", "2/3", 8) == 0b1_000_000_1_000_00011


@pytest.mark.parametrize("gain,fsr", [("2/3", 6.144), (1, 4.096), (2, 2.048), (4, 1.024), (8, 0.512), (16, 0.256)])
def test_pga_full_scale(gain, fsr) -> None:
    assert full_scale_volts(gain) == fsr
    assert counts_to_volts(32768, gain) == pytest.approx(fsr)


def test_parse_gain_spellings() -> None:
    assert parse_gain("2/3") == parse_gain(2 / 3) == parse_gain(0.667) == "2/3"
    assert parse_gain(1) == parse_gain("1") == parse_gain(None) == "1"
    assert parse_gain(16) == "16"
    for bad in (3, "32", "x", 0.5):
        with pytest.raises(ValueError):
            parse_gain(bad)


def test_parse_channel_and_data_rate() -> None:
    assert [parse_channel(c) for c in (0, "1", "A2", "ain3", None)] == ["0", "1", "2", "3", "0"]
    assert parse_channel("0-1") == "0-1" and parse_channel("2-3") == "2-3"
    for bad in (4, "1-2", "A5"):
        with pytest.raises(ValueError):
            parse_channel(bad)
    assert parse_data_rate(None) == 128 and parse_data_rate(860) == 860
    with pytest.raises(ValueError):
        parse_data_rate(100)


def test_signed16_and_counts() -> None:
    assert signed16(0x7F, 0xFF) == 32767
    assert signed16(0x80, 0x00) == -32768
    assert signed16(0xFF, 0xFF) == -1
    # 150 ohm shunt at 20 mA = 3.000 V = exactly 24000 counts at +/-4.096 V
    assert volts_to_counts(3.0, 1) == 24000
    assert volts_to_counts(0.6, 1) == 4800
    assert volts_to_counts(9.9, 1) == 32767  # saturates like the ADC


def test_single_shot_read_polls_os_bit() -> None:
    bus = FakeAds1115Bus()
    bus.set_volts(0x49, 2, 1.8)
    bus.busy_polls = 3
    sleeps = []
    adc = Ads1115(bus, 0x49, sleep=sleeps.append)
    assert adc.read_raw("2", "1", 128) == 14400
    assert bus.writes[0] == (0x49, (0x01, 0xE3, 0x83))  # config write, MUX=110 (AIN2)
    assert sleeps[0] == pytest.approx(1.1 / 128 + 0.0005)
    assert len(sleeps) == 1 + 3  # conversion wait + one poll sleep per busy read


def test_differential_and_negative_codes() -> None:
    bus = FakeAds1115Bus()
    bus.set_volts(0x48, 0, 0.5)
    bus.set_volts(0x48, 1, 1.0)
    assert Ads1115(bus, 0x48, sleep=NOSLEEP).read_raw("0-1", "1", 128) == -4000


def test_conversion_timeout_and_bad_address() -> None:
    bus = FakeAds1115Bus()
    bus.busy_polls = 10_000
    with pytest.raises(IOError, match="did not complete"):
        Ads1115(bus, 0x48, sleep=NOSLEEP).read_raw()
    with pytest.raises(ValueError, match="0x48-0x4B"):
        Ads1115(bus, 0x40)
