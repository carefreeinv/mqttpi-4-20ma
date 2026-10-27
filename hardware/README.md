# Hardware

| Design | Schematic (KiCad 9) | Images |
|--------|---------------------|--------|
| Single 4-20 mA channel | [`single-channel/ads1115-4-20ma-single.kicad_sch`](single-channel/ads1115-4-20ma-single.kicad_sch) | [SVG](single-channel/ads1115-4-20ma-single.svg), [PNG](single-channel/ads1115-4-20ma-single.png), [PDF](single-channel/ads1115-4-20ma-single.pdf) |
| 4-channel Pi add-on board (**untested**) | [`hat-4ch/ads1115-4-20ma-hat-4ch.kicad_sch`](hat-4ch/ads1115-4-20ma-hat-4ch.kicad_sch) | [SVG](hat-4ch/ads1115-4-20ma-hat-4ch.svg), [PNG](hat-4ch/ads1115-4-20ma-hat-4ch.png), [PDF](hat-4ch/ads1115-4-20ma-hat-4ch.pdf) |

The write-ups are [docs/wiring-single-channel.md](../docs/wiring-single-channel.md) (with the BOM) and
[docs/hat-reference.md](../docs/hat-reference.md).

## How these files are made

The schematics are **generated** by [`scripts/gen_schematics.py`](../scripts/gen_schematics.py). Symbols
come from KiCad's stock symbol library (`Device`, `Connector`, `Analog_ADC`, `Diode`, `power`).
The generated files open and edit normally in KiCad 9. If you edit them in KiCad, stop regenerating them.

```bash
# needs KiCad 9 (kicad-cli + kicad-symbols), rsvg-convert and Pillow
python3 scripts/gen_schematics.py --verify
```

`--verify` runs, for each sheet:

1. `kicad-cli sch export netlist`, with every expected net compared against the generator's
   intended connectivity;
2. `kicad-cli sch erc`, which must report **0 errors**;
3. SVG and PDF export, plus a PNG rendered from the SVG.

The results are written to [`verification.json`](verification.json). The committed files were
checked with kicad-cli 9.0.2: both netlist checks passed, with 0 ERC errors.

The only ERC warnings are `footprint_link_issues`. They appear because the footprint libraries
were not installed where the check ran. The footprint fields are only suggestions, and there
is no PCB layout.
