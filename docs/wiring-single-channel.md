# Single 4-20 mA channel: wiring reference

> **Untested reference design.** This drawing and the BOM below are a starting
> point that has been checked for connectivity and ERC with `kicad-cli`. No board
> has been built from it. Check every value against your own transmitter's manual
> and the component datasheets before you wire anything to a live loop.
>
> **Monitoring and telemetry only.** Gas detection is life-safety equipment. This
> circuit and the software do **not** replace a certified gas-detection controller,
> its alarm relays, or the transmitter's own alarm outputs.

![Single-channel 4-20 mA schematic](../hardware/single-channel/ads1115-4-20ma-single.png)

Files: [`.kicad_sch`](../hardware/single-channel/ads1115-4-20ma-single.kicad_sch) (KiCad 9),
[SVG](../hardware/single-channel/ads1115-4-20ma-single.svg),
[PNG](../hardware/single-channel/ads1115-4-20ma-single.png),
[PDF](../hardware/single-channel/ads1115-4-20ma-single.pdf).
Regenerate and re-check with `python scripts/gen_schematics.py --verify` (see [hardware/README.md](../hardware/README.md)).

## The loop

A 2-wire (loop-powered) transmitter is a current regulator in series with the supply. It
takes its operating power from the loop and sets the loop current between 4 and 20 mA:

The current path is:

1. +24 V
2. F1 (PTC)
3. J2.1
4. Transmitter + … transmitter −
5. J2.2 (net LOOP1−)
6. R1, the 150 Ω shunt
7. 0 V

The shunt's top node feeds AIN0 of the ADS1115 through R2. C1 and D3 sit on AIN0.

The ADS1115 measures the voltage across the low-side shunt R1, single-ended against GND.
That voltage is `V = I × R1`.

## Why 150 Ω

| Loop current | V across 150 Ω | ADS1115 code at gain 1 (±4.096 V, 125 µV/LSB) |
|-------------:|---------------:|---------------------------------------------:|
| 3.6 mA (NE43 fault limit) | 0.540 V | 4320 |
| 4.0 mA (0 %) | 0.600 V | 4800 |
| 12.0 mA (50 %) | 1.800 V | 14400 |
| 20.0 mA (100 %) | 3.000 V | 24000 |
| 21.0 mA (NE43 fault limit) | 3.150 V | 25200 |

* **It fits the PGA range and the supply.** The TI datasheet (SBAS444E) allows analog inputs
  from GND − 0.3 V to VDD + 0.3 V. With VDD = 3.3 V, the ±4.096 V range can only use about 0–3.3 V.
  At 150 Ω, 21 mA gives 3.15 V, which still leaves headroom. A 250 Ω shunt (the 1–5 V convention)
  would put 5 V on the input at 20 mA. That is outside the ADS1115's ratings at 3.3 V.
* **Resolution is ample.** The 4–20 mA span is 19 200 codes, about 0.83 µA per code, or
  about 0.005 % of span. Shunt tolerance, drift and the ADC's gain error (0.01 % typical,
  0.15 % max at 25 °C, per the datasheet) limit accuracy long before resolution does.
* **The burden is low.** At 20 mA the loop loses 3.0 V in R1, leaving the rest of the 24 V
  supply for the transmitter, F1 and the cable.
* **Power.** P = I²R = (0.020 A)² × 150 Ω = **60 mW** at 20 mA, and 66 mW at 21 mA. A 0.25 W part
  runs at about a quarter of its rating, which keeps self-heating and drift low.
  Specify 0.1 % and ≤ 25 ppm/K. A 20 K temperature swing then adds about 0.05 % at most.

### Loop budget

Check that the transmitter still gets its minimum operating voltage at 20 mA (or at 21–22 mA
if it drives over-range):

```text
V_supply(min) − I_max × (R1 + R_F1 + R_cable, both conductors) ≥ V_transmitter(min)
```

Take `V_transmitter(min)` and the transmitter's maximum loop current from its manual. Take
the PTC resistance from its datasheet. This repo does not assume values for any particular
transmitter.

## Protection

| Part | Purpose | Notes |
|------|---------|-------|
| **F1** PTC (or fast fuse) | Limits current if the field cable or transmitter shorts | With the transmitter shorted, R1 sees the full supply: 24 V / 150 Ω = **160 mA, 3.84 W**, until F1 opens. Choose I_hold above the highest normal loop current (≥ 25 mA covers NE43 levels), V_max ≥ 30 V, and a trip time the shunt's short-time overload rating can survive. If unsure, use a higher-power shunt or a fast-acting fuse. |
| **D1** TVS on LOOP+ | Clamps surges and ESD on the field wire | Standoff voltage at or above the highest supply voltage (e.g. a 28 V standoff, SMBJ28A class, for a 24 V supply). |
| **D2** TVS on LOOP− | Same, for the return wire | Same rating as D1. It must not conduct when the transmitter is shorted (LOOP− then sits at 24 V). |
| **R2** 4.7 kΩ | Limits clamp current into the ADC side | Worst case (transmitter shorted, 24 V on LOOP−): (24 − 3.6) V / 4.7 kΩ ≈ **4.3 mA**. The ADS1115 datasheet allows continuous input currents up to 10 mA. Error: R2 sits in series with the ADS1115's several-MΩ input impedance, about −0.08 % gain error, which you can calibrate out. |
| **C1** 100 nF | RC low-pass with R2: f_c = 1/(2π × 4.7 kΩ × 100 nF) ≈ 340 Hz | Attenuates RF and (partly) HART-band energy. Settles well within one 128 SPS conversion. Use C0G or X7R. |
| **D3** BAT54S (dual Schottky) | Clamps AIN0 to the 3.3 V rail and to GND | Schottky reverse leakage flows through R2 and shows up as a small offset that grows with temperature. Check the leakage spec, or use a lower-leakage clamp if you need ≤ 0.1 % accuracy at high temperature. |
| **C2** 100 nF | ADS1115 VDD decoupling | Per datasheet. |

**Power sequencing.** If the 24 V loop supply is on while the Pi's 3.3 V rail is off, AIN0 sits
above VDD. In that case D3 and the ADS1115's internal diodes back-feed the 3.3 V rail through
R2: about 0.6 mA at 20 mA loop current, and up to about 4.3 mA if the transmitter is shorted.
That is within the 10 mA input-current rating, but it does back-power the rail. It is best to
switch the loop supply together with the Pi.

## ADS1115 and Raspberry Pi side

* **U1 ADS1115** at **3.3 V**:
  * ADDR → GND gives I²C address **0x48**. ADDR → VDD/SDA/SCL gives 0x49/0x4A/0x4B.
  * Tie unused AIN1–AIN3 to GND, and leave ALERT/RDY unconnected (datasheet §9.1.4).
* **I²C:**
  * SDA → Pi pin 3 (GPIO2), SCL → Pi pin 5 (GPIO3), VDD → pin 1 (3V3), GND → pin 6.
  * The Pi has fixed **1.8 kΩ pull-ups** on GPIO2/GPIO3 ([Raspberry Pi docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)), so R3/R4 (10 kΩ) are marked DNP. Fit them only on a bus without those pull-ups.
* **Common ground:** the loop supply's 0 V, the ADS1115 GND and the Pi GND are one net.
  The measurement is single-ended to GND, so the shunt's low side **must** be that same 0 V.
* **Switchable rail (recommended):**
  * Power U1 from a 3.3 V rail that a GPIO can switch, for example through a load switch. After a brown-out, an I²C device can hold SDA low and lock up the bus. Power-cycling the ADC is the reliable way to recover.
  * Remember the sequencing note above: with U1's VDD off and the loop still powered, the inputs are back-fed through R2.

## 3-wire and 4-wire transmitters

* **3-wire** (separate supply and signal, signal sources current to 0 V):
  * Connect +24 V (after F1) to the transmitter V+.
  * Connect the transmitter 0 V/COM to GND.
  * Connect the transmitter OUT to **J2.2** (the shunt).
  * J2.1 is then not used as the loop return.
  * Put the transmitter's supply current through F1 (or its own fuse). Only the signal current flows through R1.
* **4-wire / self-powered with its own supply.** Connect its mA output + to J2.2 and its − to GND.
  This only works if the output is isolated or already shares this 0 V. Otherwise use a loop
  isolator. Never connect two different grounds through the shunt.
* **Current sinking vs sourcing.** This circuit expects current to flow *into* J2.2 and through R1
  to 0 V. A sinking 3-wire output needs a different arrangement, so check the transmitter manual.

## BOM (single channel)

Generic specs only, no prices. Footprints in the schematic are suggestions.

| Ref | Qty | Part | Specification | Notes |
|-----|----:|------|---------------|-------|
| U1 | 1 | ADS1115IDGS | 16-bit ΔΣ ADC, I²C, 4 SE / 2 diff inputs, VSSOP-10 | Breakout boards work for prototypes (check their on-board pull-ups and ADDR wiring) |
| R1 | 1 | Shunt resistor | 150 Ω, ±0.1 %, ≤ 25 ppm/K, ≥ 0.25 W | Thin-film or metal-foil; size for the short-circuit case (see F1) |
| R2 | 1 | Resistor | 4.7 kΩ, 1 %, 0603 | Input current limit and RC filter |
| R3, R4 | 2 | Resistor (DNP) | 10 kΩ, 1 %, 0603 | Only if the bus has no pull-ups |
| C1 | 1 | Capacitor | 100 nF, ≥ 16 V, C0G/X7R | Anti-alias filter |
| C2 | 1 | Capacitor | 100 nF, ≥ 16 V, X7R | VDD decoupling, close to U1 |
| D1, D2 | 2 | TVS diode | Standoff ≥ supply (e.g. 28 V for a 24 V supply), SMB | e.g. SMBJ28A class |
| D3 | 1 | Dual Schottky, series pair | BAT54S, SOT-23 | Clamp to 3.3 V / GND |
| F1 | 1 | Resettable PTC (or fast fuse) | I_hold ≥ 25 mA, V_max ≥ 30 V | See the protection table |
| J1 | 1 | Terminal block, 2-pole | 5.08 mm pitch, rated ≥ 30 V | 24 V supply in |
| J2 | 1 | Terminal block, 2-pole | 5.08 mm pitch, rated ≥ 30 V | Field loop |
| — | 1 | Loop power supply | 24 V DC, current-limited, ≥ (n × 25 mA + transmitter supply current for 3-wire units) | 0 V tied to Pi GND |

## Calibration

The driver's `scale` maps mA linearly to the published value (4 mA = `min`, 20 mA = `max`).

* For best accuracy, inject known currents with a loop calibrator, for example 4.000 and
  20.000 mA. Then set `scale.in_min`/`in_max` to the currents the driver *reports* at those
  points.
* The `<alias>_current` diagnostic entity shows the measured loop current.
