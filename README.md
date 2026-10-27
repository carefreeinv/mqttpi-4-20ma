# mqttpi-4-20ma

**4-20 mA industrial current-loop sensors for [mqttpi](https://github.com/carefreeinv/mqttpi)**,
read with an ADS1115 16-bit ADC across a precision shunt and published to Home Assistant over MQTT.

The repo contains:

* a Python **sensor driver** (`ads1115_loop`, also registered as `ads1115`) for mqttpi's sensor framework.
  It converts ADC counts → volts → mA → engineering units (4 mA = range min, 20 mA = range max) and
  detects NAMUR NE43 loop faults;
* a **single-channel wiring schematic** in KiCad, with SVG/PNG/PDF exports and a BOM;
* an **untested 4-channel Raspberry Pi add-on board** reference schematic;
* an example config, a hardware-free test suite and CI.

Typical use: loop-powered process transmitters such as gas detectors (%LEL, ppm), pressure, level,
flow and temperature, shown in Home Assistant.

> ## ⚠ Safety
>
> * **Low voltage only.** This is for 24 V DC (SELV) current loops. Do not connect mains or
>   hazardous voltages, and do not use it in hazardous (classified) areas without the
>   appropriate certified barriers and equipment.
> * **Gas detection is life-safety equipment.** This project is **monitoring and telemetry only**.
>   It does **not** replace a certified gas-detection controller, its alarm relays, or the
>   transmitter's own alarm outputs.
>   * Wire the transmitters to a certified controller for alarms.
>   * Do not rely on Home Assistant, MQTT, Wi-Fi or a Raspberry Pi for any safety function.
> * The hardware designs are **untested references**. See [Status](#status).

![Single-channel 4-20 mA schematic](hardware/single-channel/ads1115-4-20ma-single.png)

## Relation to mqttpi

mqttpi is a Raspberry Pi GPIO ↔ MQTT daemon with Home Assistant discovery. It has a pluggable
sensor framework: drivers register by type, are listed under `sensors:` in the config, and
publish to `{base_topic}/sensors/<alias>/state|attributes|availability`. mqttpi's
[`examples/analog-inputs.md`](https://github.com/carefreeinv/mqttpi/blob/main/examples/analog-inputs.md)
documents an ADS1115 `analog_input` entry (`source: ads1115`, `bus`, `address`, `channel`,
`gain`, `data_rate`, `ha.scale`) as **schema only**. This package implements that schema and
adds the 4-20 mA loop on top.

It is a **separate companion package** rather than an mqttpi patch. You install it alongside
mqttpi, and it registers its driver through mqttpi's public registry. Once it has proven
itself, the driver and the two small publishing changes below can be proposed upstream.

### mqttpi version pin

The sensor framework (`mqttpi.sensors`, the I²C bus helpers) is **not on mqttpi's `main` yet**.
It is in the stacked pull requests carefreeinv/mqttpi#12 and #14. Until those merge, this package
pins mqttpi to the **`feature/onewire-i2c-sensor-drivers`** branch (PR #14) **or later**:

```text
mqttpi @ git+https://github.com/carefreeinv/mqttpi@feature/onewire-i2c-sensor-drivers
```

That is in `pyproject.toml` and `requirements.txt`. A branch pin is a moving target. When the PRs
merge, switch it to `main` or a release tag.

## Install and run

On the Raspberry Pi (Linux, Python ≥ 3.9, I²C enabled with `raspi-config`):

```bash
python3 -m venv ~/mqttpi-venv && . ~/mqttpi-venv/bin/activate
pip install "mqttpi-4-20ma @ git+https://github.com/carefreeinv/mqttpi-4-20ma"
cp examples/gas-detector-4-20ma.yaml config.yaml     # edit device.id, mqtt.host, ranges
mqttpi-4-20ma -c config.yaml                         # same options as `mqttpi`
```

`mqttpi-4-20ma` starts the stock mqttpi daemon after three steps:

1. It registers the driver.
2. It maps `pins[]` entries with `source: ads1115` into `sensors:`.
3. It enables fault-aware publishing.

## Configuration

Use mqttpi's documented `pins[]` form (see
[`examples/gas-detector-4-20ma.yaml`](examples/gas-detector-4-20ma.yaml) and
[`.md`](examples/gas-detector-4-20ma.md)):

```yaml
pins:
  - alias: gas_lel
    direction: analog_input
    source: ads1115
    bus: 1
    address: 0x48          # 0x48-0x4B
    channel: 0             # 0-3 (A0-A3) single-ended, or "0-1" / "0-3" / "1-3" / "2-3" differential
    gain: 1                # 2/3, 1, 2, 4, 8, 16 (±6.144 ... ±0.256 V)
    data_rate: 128         # 8, 16, 32, 64, 128, 250, 475, 860 SPS
    shunt_ohms: 150        # extension
    loop: 4-20mA           # extension
    fault: { low_ma: 3.6, high_ma: 21.0 }   # extension, NAMUR NE43 defaults
    publish: { interval_ms: 2000 }
    ha:
      name: "Combustible Gas"
      unit_of_measurement: "%LEL"
      state_class: measurement
      scale: { min: 0, max: 100 }           # value at 4 mA / 20 mA
```

You can also put the same keys directly under mqttpi's `sensors:` section, with `type: ads1115_loop`.

| Key | Default | Meaning |
|-----|---------|---------|
| `shunt_ohms` | — (required with `loop`) | Shunt resistance in Ω |
| `loop` | off (voltage mode) | `4-20mA` enables loop mode |
| `fault.low_ma` / `fault.high_ma` | 3.6 / 21.0 | At or below `low_ma`: `fault_low` (open loop). At or above `high_ma`: `fault_high`. |
| `ha.scale` / `scale` | `{min: 0, max: 100, in_min: 4, in_max: 20}` | Linear map of mA to value. Reversed ranges are allowed. |
| `smoothing` | off | EMA on loop current: `0.5` or `{alpha: 0.5}`. Reset on a fault. |
| `oversample` | 1 | Conversions averaged per poll (1–64) |
| `clamp` | false | Clamp under/over-range current to 4–20 mA before scaling |
| `supply_v` | 3.3 | ADS1115 VDD, used to warn when the fault-high voltage exceeds the usable input range |

All extensions are optional keys. A config with no extensions behaves as mqttpi documents
(volts, scaled by `ha.scale`).

### Published entities and faults

Each loop pin `X` publishes:

* `X`: the engineering value;
* `X_current`: loop current in mA (diagnostic);
* `X_loop_fault`: 0 ok, 1 under range, 2 over range, 3 fault low, 4 fault high (diagnostic).

`…/attributes` carries `loop_status`, `current_ma`, `voltage`, `raw`, `quality` and `timestamp`.

On a **fault**, the value is withheld: `X`'s availability topic goes `offline`, so Home Assistant
shows *unavailable*, not a stale or zero concentration. `X_current` and `X_loop_fault` stay
online, so you can see why. When the loop recovers, `X` comes back online.

### Upstream proposals

Two behaviours are implemented here by subclassing mqttpi's `SensorSubsystem`. The `mqttpi-4-20ma`
command swaps the subclass in by patching `mqttpi.daemon.SensorSubsystem`. Both are small and
are intended for upstream:

1. A reading key that is present but `None` marks that entity unavailable. Today mqttpi skips
   the value and keeps the last state.
2. Driver-provided `Reading.attributes` are merged into `…/attributes`.

The ADS1115 driver itself corresponds to step 5 of the plan in mqttpi PR #14
(`.plans/review-needed/onewire-i2c-sensor-drivers.md`).

## Wiring summary

Full details, protection math and BOM are in [docs/wiring-single-channel.md](docs/wiring-single-channel.md).

* **Loop:** 24 V supply → PTC → transmitter (+/−) → **150 Ω 0.1 % 25 ppm** shunt → 0 V.
  * 20 mA gives 3.0 V and 60 mW in the shunt.
* **Protection:**
  * TVS on both field wires;
  * 4.7 kΩ + 100 nF RC filter (≈ 340 Hz);
  * BAT54S clamp to 3.3 V / GND.
* **ADC:** ADS1115 at **3.3 V**, ADDR → GND (**0x48**), SDA/SCL to Pi pins 3/5.
  * The Pi's own 1.8 kΩ pull-ups are used; the external pull-ups are DNP.
* **Common ground:** the loop supply's 0 V is the Pi's GND.
* **Recommended:** power the ADS1115 from a GPIO-switchable 3.3 V rail, so a bus that is stuck
  after a brown-out can be recovered by power-cycling it. Switch the 24 V loop supply together with
  the Pi so the inputs don't back-feed an unpowered ADC.
* **3-wire and 4-wire transmitters:** see the notes in the wiring doc.

4-channel add-on board reference (**untested**, not HAT+-compliant) and commercial alternatives:
[docs/hat-reference.md](docs/hat-reference.md).

![4-channel reference schematic](hardware/hat-4ch/ads1115-4-20ma-hat-4ch.png)

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q        # hardware-free: fake I2C bus and fake MQTT client
ruff check .
```

* The tests cover:
  * ADS1115 register encoding;
  * count → V → mA → unit math;
  * NE43 fault classification;
  * config mapping, checked by mqttpi's own parser;
  * HA discovery, against the snapshot `tests/snapshots/gas_discovery.json`;
  * fault and availability publishing.
* To regenerate the snapshot, run with `MQTTPI_UPDATE_SNAPSHOTS=1`.
* CI runs ruff and pytest on Python 3.9, 3.11 and 3.13.
* Schematics are regenerated and checked with `python3 scripts/gen_schematics.py --verify`, which
  needs KiCad 9. See [hardware/README.md](hardware/README.md).

## Status

| Part | Status |
|------|--------|
| Driver, config mapping, HA publishing | Implemented, unit-tested with fakes. **Not yet tested on real hardware or transmitters.** |
| Single-channel schematic | KiCad netlist check and ERC pass (kicad-cli 9.0.2). Not built. |
| 4-channel board | **Untested reference schematic.** No PCB layout, no ID EEPROM (not HAT+-compliant), no isolation. |
| Pico W | Not supported by this plugin, which needs mqttpi's Linux daemon. See [hat-reference.md](docs/hat-reference.md#pico-w-compatibility). |

## License

[MIT](LICENSE)
