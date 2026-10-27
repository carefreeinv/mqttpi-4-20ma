#!/usr/bin/env python3
"""Generate, check and render the KiCad schematics in hardware/.

    python scripts/gen_schematics.py            # write .kicad_sch/.kicad_pro only
    python scripts/gen_schematics.py --verify   # + kicad-cli netlist/ERC check, SVG/PDF/PNG export

The schematics are ordinary KiCad 9 files: open and edit them in Eeschema. This
script exists so the drawings are reproducible and so CI-free connectivity
checks (netlist vs. the intended nets below) back up the pictures.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kicad_gen import Sheet, parse_netlist  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HW = os.path.join(ROOT, "hardware")
DATE = "2026-10-27"

FP_R = "Resistor_SMD:R_1206_3216Metric"
FP_R_SMALL = "Resistor_SMD:R_0603_1608Metric"
FP_C = "Capacitor_SMD:C_0603_1608Metric"
FP_TVS = "Diode_SMD:D_SMB"
FP_PTC = "Fuse:Fuse_1206_3216Metric"
FP_BAT54S = "Package_TO_SOT_SMD:SOT-23"
FP_TERM2 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"

V_SHUNT = "150R 0.1%"
V_SERIES = "4.7k 1%"
V_FILT = "100nF"
V_TVS = "TVS 28V"
V_PTC = "PTC 30V"
V_CLAMP = "BAT54S"
V_ADC = "ADS1115IDGS"
V_PI = "Raspberry Pi 40-pin header"

# Pi 40-pin header pins the drawings use; everything else gets a no-connect flag.
PI_GND = ["6", "9", "14", "20", "25", "30", "34", "39"]


def vertical_r(sh, lib, ref, val, x, y, fp, **kw):
    return sh.add(lib, ref, val, (x, y), footprint=fp, ref_at=(2.54, -1.27, "left"), val_at=(2.54, 1.27, "left"), **kw)


def tvs_vertical(sh, ref, x, y):
    # D_TVS is drawn horizontally; rotate so pin 2 is on top, pin 1 at the bottom.
    return sh.add("Device:D_TVS", ref, V_TVS, (x, y), rot=90, footprint=FP_TVS,
                  ref_at=(3.81, -1.27, "right"), val_at=(3.81, 1.27, "right"))


def loop_channel(sh, n, x0, y0, ain_label, refs, *, stub=True):
    """One protected 4-20 mA input, field terminal on the left, AIN label on the right.

    Returns the dict of intended nets (ref.pin sets) for this channel.
    """
    f, j, d_p, d_n, rsh, rs, c, dcl = refs
    # field terminal, mirrored so the screw pins face right
    term = sh.add("Connector:Screw_Terminal_01x02", j, f"Loop {n} (+ / -)", (x0, y0), mirror=True,
                  footprint=FP_TERM2, ref_at=(-2.54, -3.81, "right"), val_at=(-2.54, 6.35, "right"))
    p1, p2 = term.pin("1"), term.pin("2")          # p1 = LOOP+ out to transmitter, p2 = LOOP- return
    # PTC from the 24 V rail down to LOOP+
    fx = p1[0] + 12.7
    ptc = sh.add("Device:Polyfuse", f, V_PTC, (fx, p1[1] - 7.62), footprint=FP_PTC,
                 ref_at=(2.54, -1.27, "left"), val_at=(2.54, 1.27, "left"))
    sh.wire(ptc.pin("1"), (fx, ptc.pin("1")[1] - 2.54))
    sh.power("+24V", (fx, ptc.pin("1")[1] - 2.54))
    sh.wire(ptc.pin("2"), (fx, p1[1]), p1)
    # LOOP+ TVS
    dpx = fx + 15.24
    dp = tvs_vertical(sh, d_p, dpx, p1[1] + 6.35)
    sh.wire((fx, p1[1]), (dpx, p1[1]), dp.pin("2"))
    sh.power("GND", (dpx, dp.pin("1")[1] + 2.54))
    sh.wire(dp.pin("1"), (dpx, dp.pin("1")[1] + 2.54))
    sh.label(f"LOOP{n}+", (fx + 7.62, p1[1]))
    # LOOP- return: TVS, shunt, series resistor
    ret_y = p2[1] + 12.7
    retx = p2[0] + 7.62
    sh.wire(p2, (retx, p2[1]), (retx, ret_y))
    sh.label(f"LOOP{n}-", (retx, ret_y), justify="left bottom")
    dnx = retx + 7.62
    dn = tvs_vertical(sh, d_n, dnx, ret_y + 7.62)
    sh.wire((retx, ret_y), (dnx, ret_y), dn.pin("2"))
    sh.wire(dn.pin("1"), (dnx, dn.pin("1")[1] + 2.54))
    sh.power("GND", (dnx, dn.pin("1")[1] + 2.54))
    shx = dnx + 20.32
    sh_ = sh.add("Device:R", rsh, V_SHUNT, (shx, ret_y + 7.62), footprint=FP_R,
                 ref_at=(2.54, -1.27, "left"), val_at=(2.54, 1.27, "left"))
    sh.wire((dnx, ret_y), (shx, ret_y), sh_.pin("1"))
    sh.wire(sh_.pin("2"), (shx, sh_.pin("2")[1] + 2.54))
    sh.power("GND", (shx, sh_.pin("2")[1] + 2.54))
    rsx = shx + 17.78
    rs_ = sh.add("Device:R", rs, V_SERIES, (rsx, ret_y), rot=90, footprint=FP_R_SMALL,
                 ref_at=(0, -2.54, "center"), val_at=(0, 2.54, "center"))
    sh.wire((shx, ret_y), rs_.pin("1"))
    # filter cap and clamp on the ADC side
    cx = rsx + 10.16
    c_ = sh.add("Device:C", c, V_FILT, (cx, ret_y + 7.62), footprint=FP_C,
                ref_at=(2.54, -1.27, "left"), val_at=(2.54, 1.27, "left"))
    sh.wire(rs_.pin("2"), (cx, ret_y), c_.pin("1"))
    sh.wire(c_.pin("2"), (cx, c_.pin("2")[1] + 2.54))
    sh.power("GND", (cx, c_.pin("2")[1] + 2.54))
    dx = cx + 22.86
    clamp = sh.add("Diode:BAT54S", dcl, V_CLAMP, (dx, ret_y + 10.16), rot=180, footprint=FP_BAT54S,
                   ref_at=(0, 3.81, "center"), val_at=(0, 6.35, "center"))
    com, k, a = clamp.pin("3"), clamp.pin("2"), clamp.pin("1")   # rot 180: K on the left, A on the right
    sh.wire((cx, ret_y), (dx, ret_y), com)
    sh.power("+3V3", k)
    sh.wire(a, (a[0], a[1] + 5.08))
    sh.power("GND", (a[0], a[1] + 5.08))
    end = (dx + 12.7, ret_y) if stub else (dx + 7.62, ret_y)
    sh.wire((dx, ret_y), end)
    sh.label(ain_label, (dx + 5.08, ret_y) if stub else end)
    nets = {
        f"LOOP{n}+": {f"{f}.2", f"{j}.1", f"{d_p}.2"},
        f"LOOP{n}-": {f"{j}.2", f"{d_n}.2", f"{rsh}.1", f"{rs}.1"},
        ain_label: {f"{rs}.2", f"{c}.1", f"{dcl}.3"},
        "GND": {f"{d_p}.1", f"{d_n}.1", f"{rsh}.2", f"{c}.2", f"{dcl}.1"},
        "+3V3": {f"{dcl}.2"},
        "+24V": {f"{f}.1"},
    }
    return nets, end


def merge(into, more):
    for k, v in more.items():
        into.setdefault(k, set()).update(v)


def adc_and_pi(sh, ux, uy, pix, piy, ain_nets, *, pullups=("R3", "R4"), cdec="C2", ain_labels=True):
    """ADS1115 (addr 0x48) + optional I2C pull-ups + Pi header, joined by net labels."""
    nets = {}
    u = sh.add("Analog_ADC:ADS1115IDGS", "U1", V_ADC, (ux, uy), footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
               ref_at=(2.54, -15.24, "left"), val_at=(2.54, -12.7, "left"))
    ain_pins = {"AIN0": "4", "AIN1": "5", "AIN2": "6", "AIN3": "7"}
    for name, pin in ain_pins.items():
        pt = u.pin(pin)
        if name in ain_nets:
            if ain_labels:
                sh.wire(pt, (pt[0] - 7.62, pt[1]))
                sh.label(name, (pt[0] - 7.62, pt[1]), justify="right bottom")
            nets.setdefault(name, set()).add(f"U1.{pin}")
        else:  # datasheet 9.1.4: float unused inputs or tie them to GND
            gx = pt[0] - 5.08
            sh.wire(pt, (gx, pt[1]))
            nets.setdefault("GND", set()).add(f"U1.{pin}")
    unused = [p for n, p in ain_pins.items() if n not in ain_nets]
    if unused:
        gx = u.pin(unused[0])[0] - 5.08
        ys = [u.pin(p)[1] for p in unused]
        bottom = u.pin("3")[1] + 2.54
        sh.wire((gx, min(ys)), (gx, bottom))
        sh.power("GND", (gx, bottom))
    # supply + decoupling
    vdd = u.pin("8")
    top = vdd[1] - 7.62
    sh.wire(vdd, (vdd[0], top))
    sh.power("+3V3", (vdd[0], top))
    cx = vdd[0] - 17.78
    c = sh.add("Device:C", cdec, "100nF", (cx, top + 7.62), footprint=FP_C,
               ref_at=(-2.54, -1.27, "right"), val_at=(-2.54, 1.27, "right"))
    sh.wire((vdd[0], top + 2.54), (cx, top + 2.54), c.pin("1"))
    sh.wire(c.pin("2"), (cx, c.pin("2")[1] + 2.54))
    sh.power("GND", (cx, c.pin("2")[1] + 2.54))
    gnd = u.pin("3")
    sh.wire(gnd, (gnd[0], gnd[1] + 2.54))
    sh.power("GND", (gnd[0], gnd[1] + 2.54))
    # ADDR -> GND = 0x48
    addr = u.pin("1")
    ax = addr[0] + 5.08
    sh.wire(addr, (ax, addr[1]), (ax, gnd[1] + 2.54))
    sh.power("GND", (ax, gnd[1] + 2.54))
    sh.text("ADDR=GND -> 0x48", (ax + 1.27, addr[1] + 1.27))
    # I2C with optional pull-ups (the Pi already pulls SDA/SCL up on-board)
    scl, sda = u.pin("10"), u.pin("9")
    r_scl, r_sda = pullups
    sh.text(f"{r_scl}/{r_sda}: DNP (Pi has on-board\nI2C pull-ups)", (scl[0] + 12.7, scl[1] - 27.94))
    for ref, pt, name, dx in ((r_scl, scl, "SCL", 15.24), (r_sda, sda, "SDA", 27.94)):
        x = pt[0] + dx
        rr = sh.add("Device:R", ref, "10k", (x, scl[1] - 12.7), footprint=FP_R_SMALL, dnp=True,
                    ref_at=(2.54, -1.27, "left"), val_at=(2.54, 1.27, "left"))
        sh.wire(rr.pin("1"), (x, rr.pin("1")[1] - 2.54))
        sh.power("+3V3", (x, rr.pin("1")[1] - 2.54))
        sh.wire(pt, (x, pt[1]))
        sh.wire(rr.pin("2"), (x, pt[1]))
        sh.wire((x, pt[1]), (x + 10.16, pt[1]))
        sh.label(name, (x + 10.16, pt[1]))
        nets[name] = {f"U1.{'10' if name == 'SCL' else '9'}", f"{ref}.2"}
        nets.setdefault("+3V3", set()).add(f"{ref}.1")
    # Pi header, mirrored so the GPIO2/3 side faces left
    pi = sh.add("Connector:Raspberry_Pi_2_3", "J10", V_PI, (pix, piy), mirror=True,
                footprint="Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical",
                ref_at=(10.16, -36.83, "left"), val_at=(-20.32, 41.91, "left"))
    for name, pin in (("SDA", "3"), ("SCL", "5")):
        pt = pi.pin(pin)
        sh.wire(pt, (pt[0] - 7.62, pt[1]))
        sh.label(name, (pt[0] - 7.62, pt[1]), justify="right bottom")
        nets[name].add(f"J10.{pin}")
    p33 = pi.pin("1")
    sh.wire(p33, (p33[0], p33[1] - 2.54))
    sh.power("+3V3", (p33[0], p33[1] - 2.54))
    sh.power("PWR_FLAG", (p33[0] - 7.62, p33[1] - 2.54))
    sh.wire((p33[0], p33[1] - 2.54), (p33[0] - 7.62, p33[1] - 2.54))
    pg = pi.pin("6")
    sh.wire(pg, (pg[0], pg[1] + 2.54))
    sh.power("GND", (pg[0], pg[1] + 2.54))
    sh.text("Pi 5 V (pins 2/4) not used", (pi.pin("2")[0] + 7.62, pi.pin("2")[1] - 7.62))
    nets.setdefault("+3V3", set()).update({"U1.8", f"{cdec}.1", "J10.1", "J10.17"})
    nets.setdefault("GND", set()).update({"U1.3", "U1.1", f"{cdec}.2"} | {f"J10.{p}" for p in PI_GND})
    nc = u.pin("2")  # ALERT/RDY unused (datasheet 9.1.4: leave unconnected)
    sh.nc(nc)
    return nets


def supply_input(sh, x, y, ref="J1", tvs=None):
    """24 V loop-supply input terminal; returns rail end point and nets."""
    j = sh.add("Connector:Screw_Terminal_01x02", ref, "24V DC in (+ / 0V)", (x, y), mirror=True,
               footprint=FP_TERM2, ref_at=(-2.54, -3.81, "right"), val_at=(-2.54, 6.35, "right"))
    p1, p2 = j.pin("1"), j.pin("2")
    sh.wire(p1, (p1[0] + 12.7, p1[1]))
    sh.power("+24V", (p1[0] + 5.08, p1[1]))
    sh.power("PWR_FLAG", (p1[0] + 12.7, p1[1]))
    gx = p2[0] + 5.08
    sh.wire(p2, (gx, p2[1]), (gx, p2[1] + 7.62))
    sh.power("GND", (gx, p2[1] + 7.62))
    sh.wire((gx, p2[1] + 5.08), (gx + 7.62, p2[1] + 5.08))
    sh.power("PWR_FLAG", (gx + 7.62, p2[1] + 5.08), rot=180)
    nets = {"+24V": {f"{ref}.1"}, "GND": {f"{ref}.2"}}
    end = (p1[0] + 12.7, p1[1])
    if tvs:
        tx = p1[0] + 20.32
        d = tvs_vertical(sh, tvs, tx, p1[1] + 6.35)
        sh.wire(end, (tx, p1[1]), d.pin("2"))
        sh.wire(d.pin("1"), (tx, d.pin("1")[1] + 2.54))
        sh.power("GND", (tx, d.pin("1")[1] + 2.54))
        nets["+24V"].add(f"{tvs}.2")
        nets["GND"].add(f"{tvs}.1")
        end = (tx, p1[1])
    return end, nets


SINGLE_NOTES = """\
Single 4-20 mA channel - wiring reference (NOT a tested product)
Loop: +24 V -> F1 -> J2.1 -> 2-wire transmitter -> J2.2 -> R1 shunt -> 0 V
Shunt R1 = 150 R: 20 mA -> 3.00 V, 4 mA -> 0.60 V, 3.6 mA -> 0.54 V, 21 mA -> 3.15 V
  ADS1115 PGA +/-4.096 V (gain 1) at VDD 3.3 V: inputs above VDD+0.3 V are not allowed, so
  full scale is effectively 3.3 V; 21 mA still leaves 150 mV headroom.
  20 mA: P = I^2 R = 60 mW (use >= 0.25 W, 0.1 %, 25 ppm/K or better).
  Loop burden at 20 mA: 3.0 V across R1 (+ PTC/wiring); transmitter sees the rest of 24 V.
R2 + C1: fc = 1/(2 pi 4.7k 100n) ~ 340 Hz anti-alias / RF filter; R2 limits clamp current:
  worst case (transmitter shorted, 24 V on LOOP-): (24 - 3.6) V / 4.7k ~ 4.3 mA < 10 mA abs-max.
D3 (BAT54S) clamps AIN0 to the 3.3 V rail / GND; D1, D2 absorb field surges (choose standoff >= supply).
F1 PTC: with a shorted transmitter R1 sees 24 V / 150 R = 160 mA (3.8 W) until F1 trips - pick a
  PTC/fuse that opens fast enough for the shunt's pulse rating (see docs/wiring-single-channel.md).
3-wire transmitter: +24 V -> sensor V+, sensor 0 V -> GND (0 V), sensor OUT -> J2.2. J2.1 not used.
4-wire / self-powered: connect its loop output + to J2.2 and its - to GND only if its output
  is isolated or shares this 0 V; otherwise use a loop isolator.
Common ground: the 24 V supply 0 V, ADS1115 GND and Pi GND are one net.
Recommended: power U1 from a GPIO-switchable 3.3 V rail so the I2C bus can be recovered after a brown-out.
R3/R4 are DNP: the Raspberry Pi already has pull-ups on GPIO2/GPIO3."""


def build_single():
    sh = Sheet("ads1115-4-20ma-single", "ADS1115 + one 4-20 mA loop (2-wire) - reference wiring",
               paper="A4", date=DATE, rev="0.1",
               comments=["Untested reference design - verify against datasheets before building",
                         "Monitoring/telemetry only - not a certified gas-detection controller",
                         "mqttpi-4-20ma - MIT licensed"])
    nets: dict[str, set[str]] = {}
    end, n = supply_input(sh, 25.4, 30.48, "J1")
    merge(nets, n)
    chan_nets, ain_end = loop_channel(sh, 1, 25.4, 58.42, "AIN0",
                                      ("F1", "J2", "D1", "D2", "R1", "R2", "C1", "D3"))
    merge(nets, chan_nets)
    sh.text("Field: 2-wire transmitter\n(e.g. gas detector head)\nJ2.1 -> transmitter +\nJ2.2 <- transmitter -",
            (5.08, 78.74), size=1.5)
    ux = ain_end[0] + 20.32
    uy = ain_end[1] + 2.54          # AIN0 (symbol y +2.54) lines up with the AIN0 net
    sh.wire(ain_end, (ux - 10.16, ain_end[1]))
    merge(nets, adc_and_pi(sh, ux, uy, ux + 104.14, uy + 7.62, {"AIN0"}, ain_labels=False))
    sh.text(SINGLE_NOTES, (12.7, 124.46), size=1.5)
    return sh, nets


HAT_NOTES = """\
4-channel 4-20 mA input board for the Raspberry Pi 40-pin header - UNTESTED REFERENCE DESIGN
Each channel: PTC -> field terminal (+) -> transmitter -> field terminal (-) -> 150 R shunt -> 0 V,
  with TVS on both field wires, 4.7k/100n RC filter and BAT54S clamp in front of the ADS1115 input.
ADS1115 at 0x48 (ADDR = GND); move ADDR to VDD/SDA/SCL for 0x49/0x4A/0x4B to stack boards.
One external 24 V supply feeds all loops through J5; its 0 V is the Pi GND (non-isolated design).
Not included: HAT+ ID EEPROM, mounting/keep-out, reverse-polarity protection, isolation,
  GPIO-switchable 3.3 V rail for U1 (recommended for I2C recovery after a brown-out)."""


def build_hat():
    sh = Sheet("ads1115-4-20ma-hat-4ch", "4-channel 4-20 mA Pi HAT - reference design (untested)",
               paper="A3", date=DATE, rev="0.1",
               comments=["UNTESTED reference design - no board has been built from this file",
                         "Monitoring/telemetry only - not a certified gas-detection controller",
                         "mqttpi-4-20ma - MIT licensed"])
    nets: dict[str, set[str]] = {}
    end, n = supply_input(sh, 25.4, 30.48, "J5", tvs="D20")
    merge(nets, n)
    for i in range(4):
        y = 66.04 + i * 55.88
        refs = (f"F{i + 1}", f"J{i + 1}", f"D{2 * i + 1}", f"D{2 * i + 2}", f"R{i + 1}", f"R{i + 11}",
                f"C{i + 1}", f"D{i + 11}")
        cn, _e = loop_channel(sh, i + 1, 25.4, y, f"AIN{i}", refs, stub=False)
        merge(nets, cn)
    merge(nets, adc_and_pi(sh, 190.5, 101.6, 322.58, 111.76, {"AIN0", "AIN1", "AIN2", "AIN3"},
                           pullups=("R21", "R22"), cdec="C10"))
    sh.text(HAT_NOTES, (162.56, 177.8), size=1.8)
    return sh, nets


def check_nets(expected: dict[str, set[str]], actual: dict[str, set[str]]) -> list[str]:
    errors = []
    by_node = {node: name for name, nodes in actual.items() for node in nodes}
    for name, nodes in expected.items():
        homes = {by_node.get(n) for n in nodes}
        if None in homes:
            errors.append(f"{name}: unconnected {[n for n in nodes if n not in by_node]}")
        homes.discard(None)
        if len(homes) > 1:
            errors.append(f"{name}: split across {sorted(homes)}")
        for home in homes:
            extra = actual[home] - nodes
            if extra:
                errors.append(f"{name}: unexpected members {sorted(extra)}")
    return errors


def run(cmd):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def crop_svg(svg: str, tmp: str, margin_mm: float = 6.0) -> None:
    """Shrink the exported page to the drawn content so the PNG/SVG read well on GitHub."""
    import re

    from PIL import Image, ImageChops

    with open(svg, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r'viewBox="0\.0000 0\.0000 ([0-9.]+) ([0-9.]+)"', text)
    w_page, h_page = float(m.group(1)), float(m.group(2))

    dpi = 200
    full = os.path.join(tmp, "full.png")
    run(["rsvg-convert", "-d", str(dpi), "-p", str(dpi), "-o", full, svg])
    img = Image.open(full).convert("RGB")
    edge = 8  # ignore anti-aliased page edges
    inner = img.crop((edge, edge, img.width - edge, img.height - edge))
    bg = Image.new("RGB", inner.size, inner.getpixel((2, 2)))
    diff = ImageChops.difference(inner, bg).convert("L").point(lambda v: 255 if v > 12 else 0)
    box = diff.getbbox()
    if box:
        box = (box[0] + edge, box[1] + edge, box[2] + edge, box[3] + edge)
    if not box:
        return
    mm = 25.4 / dpi
    x0, y0 = max(box[0] * mm - margin_mm, 0), max(box[1] * mm - margin_mm, 0)
    x1, y1 = min(box[2] * mm + margin_mm, w_page), min(box[3] * mm + margin_mm, h_page)
    w, h = x1 - x0, y1 - y0
    new = f'width="{w:.3f}mm" height="{h:.3f}mm" viewBox="{x0:.3f} {y0:.3f} {w:.3f} {h:.3f}"'
    text = re.sub(r'width="[^"]+mm" height="[^"]+mm" viewBox="[^"]+"', new, text, count=1)
    with open(svg, "w", encoding="utf-8") as fh:
        fh.write(text)


def verify(sh, nets, outdir) -> dict:
    sch = os.path.join(outdir, f"{sh.name}.kicad_sch")
    report = {"schematic": os.path.relpath(sch, ROOT)}
    cli = shutil.which("kicad-cli")
    if not cli:
        report["verified"] = False
        return report
    report["kicad_cli"] = run([cli, "version"]).stdout.strip()
    tmp = os.path.join(outdir, ".build")
    os.makedirs(tmp, exist_ok=True)
    net = os.path.join(tmp, "netlist.net")
    res = run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", net, sch])
    if res.returncode:
        raise SystemExit(res.stdout + res.stderr)
    errs = check_nets(nets, parse_netlist(net))
    report["netlist_check"] = "pass" if not errs else errs
    erc = os.path.join(tmp, "erc.json")
    run([cli, "sch", "erc", "--format", "json", "--severity-all", "-o", erc, sch])
    with open(erc, encoding="utf-8") as fh:
        data = json.load(fh)
    viol = [v for s in data.get("sheets", []) for v in s.get("violations", [])]
    report["erc"] = {
        "errors": sum(1 for v in viol if v.get("severity") == "error"),
        "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
        "warning_types": sorted({v.get("type") for v in viol if v.get("severity") == "warning"}),
        "details": sorted({f"{v.get('severity')}: {v.get('type')}: {v.get('description')}"
                           + ("" if v.get("severity") != "error" else
                              " @ " + "; ".join(i.get("description", "") for i in v.get("items", [])))
                           for v in viol}),
    }
    svgdir = os.path.join(tmp, "svg")
    res = run([cli, "sch", "export", "svg", "--exclude-drawing-sheet", "-o", svgdir, sch])
    if res.returncode:
        raise SystemExit(res.stdout + res.stderr)
    svg = os.path.join(svgdir, f"{sh.name}.svg")
    shutil.copy(svg, os.path.join(outdir, f"{sh.name}.svg"))
    run([cli, "sch", "export", "pdf", "-o", os.path.join(outdir, f"{sh.name}.pdf"), sch])
    if shutil.which("rsvg-convert"):
        crop_svg(os.path.join(outdir, f"{sh.name}.svg"), tmp)
        png = os.path.join(outdir, f"{sh.name}.png")
        run(["rsvg-convert", "-w", "2400", "-o", png, os.path.join(outdir, f"{sh.name}.svg")])
    shutil.rmtree(tmp)
    report["verified"] = report["netlist_check"] == "pass" and report["erc"]["errors"] == 0
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true", help="netlist/ERC check and SVG/PDF/PNG export via kicad-cli")
    args = ap.parse_args(argv)
    reports = []
    for build, sub in ((build_single, "single-channel"), (build_hat, "hat-4ch")):
        sh, nets = build()
        sh.finalize()
        outdir = os.path.join(HW, sub)
        print("wrote", sh.write(outdir))
        if args.verify:
            reports.append(verify(sh, nets, outdir))
    if args.verify:
        text = json.dumps(reports, indent=2) + "\n"
        print(text)
        with open(os.path.join(HW, "verification.json"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return 0 if all(r.get("verified") for r in reports) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
