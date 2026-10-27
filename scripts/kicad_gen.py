"""Tiny KiCad 9 schematic writer used by gen_schematics.py.

Symbols are copied verbatim from the stock KiCad libraries (flattening derived
symbols), parts are placed by coordinate, and wires are given explicitly. The
writer splits wires at every connection point and adds junctions, so the file
matches what Eeschema itself would save. Connectivity is then proven by
exporting a netlist with kicad-cli and comparing it to the intended nets.
"""

from __future__ import annotations

import copy
import os
import uuid as _uuid

from sexp import Sym, dump, find, first, parse

LIBDIR = os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
KICAD_SCH_VERSION = 20250114  # KiCad 9.0 file format
_NS = _uuid.UUID("6f1c3c39-2b7e-4c55-9a43-6a0b1d6f4b20")
_libcache: dict[str, list] = {}


def r(v: float) -> float:
    return round(v + 0.0, 4)


def fmt(v: float):
    v = r(v)
    return Sym(f"{v:g}") if v != int(v) else Sym(str(int(v)))


class Uuid:
    def __init__(self, seed: str):
        self.seed = seed
        self.n = 0

    def __call__(self, tag: str = "") -> str:
        self.n += 1
        return str(_uuid.uuid5(_NS, f"{self.seed}/{tag}/{self.n}"))


def _lib(name: str) -> list:
    if name not in _libcache:
        with open(os.path.join(LIBDIR, f"{name}.kicad_sym"), encoding="utf-8") as fh:
            _libcache[name] = parse(fh.read())
    return _libcache[name]


def load_symbol(lib_id: str) -> list:
    """Return a flattened copy of a library symbol, named ``lib:sym``."""
    lib, name = lib_id.split(":")
    syms = {s[1]: s for s in find(_lib(lib), "symbol")}
    sym = copy.deepcopy(syms[name])
    ext = first(sym, "extends")
    if ext is not None:
        parent = copy.deepcopy(syms[ext[1]])
        props = {p[1]: p for p in find(sym, "property")}
        out = [Sym("symbol"), lib_id]
        for item in parent[2:]:
            if isinstance(item, list) and item and item[0] == "property" and item[1] in props:
                out.append(props[item[1]])
            elif isinstance(item, list) and item and item[0] == "symbol":
                item[1] = item[1].replace(ext[1], name, 1)
                out.append(item)
            else:
                out.append(item)
        return out
    sym[1] = lib_id
    return sym


def symbol_pins(sym: list) -> dict[str, tuple[float, float]]:
    pins = {}
    for unit in find(sym, "symbol"):
        for p in find(unit, "pin"):
            at = first(p, "at")
            pins[first(p, "number")[1]] = (float(at[1]), float(at[2]))
    return pins


def symbol_pin_types(sym: list) -> dict[str, str]:
    return {first(p, "number")[1]: str(p[1]) for u in find(sym, "symbol") for p in find(u, "pin")}


def _font(size=1.27, bold=False):
    f = [Sym("font"), [Sym("size"), fmt(size), fmt(size)]]
    if bold:
        f.append([Sym("bold"), Sym("yes")])
    return f


def _effects(size=1.27, justify=None, hide=False, bold=False):
    e = [Sym("effects"), _font(size, bold)]
    justify = " ".join(j for j in (justify or "").split() if j != "center")
    if justify:
        e.append([Sym("justify")] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym("hide"), Sym("yes")])
    return e


class Part:
    def __init__(self, sheet, lib_id, ref, value, at, rot=0, mirror=False, footprint="", fields=None,
                 ref_at=None, val_at=None, unit=1, dnp=False, in_bom=True):
        self.sheet, self.lib_id, self.ref, self.value = sheet, lib_id, ref, value
        self.x, self.y = at
        self.rot, self.mirror, self.footprint = rot % 360, mirror, footprint
        self.fields = fields or {}
        self.ref_at, self.val_at, self.unit, self.dnp, self.in_bom = ref_at, val_at, unit, dnp, in_bom
        self.sym = sheet.use_symbol(lib_id)
        self.pins_local = symbol_pins(self.sym)
        self.pin_types = symbol_pin_types(self.sym)
        self.is_power = first(self.sym, "power") is not None

    def pin(self, num: str) -> tuple[float, float]:
        px, py = self.pins_local[str(num)]
        if self.mirror:
            px = -px
        sx, sy = px, -py
        for _ in range(self.rot // 90):
            sx, sy = sy, -sx
        return (r(self.x + sx), r(self.y + sy))

    def _prop(self, name, value, at, justify=None, hide=False, angle=None):
        x, y = at
        if angle is None:
            angle = self.rot % 180  # KiCad stores field angles in the rotated frame
        if justify and self.mirror:  # fields of a mirrored symbol are mirrored too
            justify = " ".join({"left": "right", "right": "left"}.get(j, j) for j in justify.split())
        return [Sym("property"), name, value, [Sym("at"), fmt(x), fmt(y), fmt(angle)],
                _effects(justify=justify, hide=hide)]

    def to_sexp(self, project, root_uuid):
        u = self.sheet.uid(self.ref)
        node = [Sym("symbol"), [Sym("lib_id"), self.lib_id],
                [Sym("at"), fmt(self.x), fmt(self.y), fmt(self.rot)]]
        if self.mirror:
            node.append([Sym("mirror"), Sym("y")])
        node += [[Sym("unit"), self.unit], [Sym("exclude_from_sim"), Sym("no")],
                 [Sym("in_bom"), Sym("yes" if self.in_bom and not self.is_power else "no")],
                 [Sym("on_board"), Sym("yes")], [Sym("dnp"), Sym("yes" if self.dnp else "no")],
                 [Sym("uuid"), u]]
        hide_ref = self.is_power
        ra = self.ref_at or (2.54, -1.27, "left")
        va = self.val_at or (2.54, 1.27, "left")
        node.append(self._prop("Reference", self.ref, (self.x + ra[0], self.y + ra[1]),
                               justify=ra[2] if len(ra) > 2 else None, hide=hide_ref))
        node.append(self._prop("Value", self.value, (self.x + va[0], self.y + va[1]),
                               justify=va[2] if len(va) > 2 else None,
                               hide=bool(va[3]) if len(va) > 3 else False))
        node.append(self._prop("Footprint", self.footprint, (self.x, self.y), hide=True))
        ds = next((p[2] for p in find(self.sym, "property") if p[1] == "Datasheet"), "~")
        node.append(self._prop("Datasheet", self.fields.pop("Datasheet", ds), (self.x, self.y), hide=True))
        desc = next((p[2] for p in find(self.sym, "property") if p[1] == "Description"), "")
        node.append(self._prop("Description", self.fields.pop("Description", desc), (self.x, self.y), hide=True))
        for k, v in self.fields.items():
            node.append(self._prop(k, v, (self.x, self.y), hide=True))
        for num in self.pins_local:
            node.append([Sym("pin"), num, [Sym("uuid"), self.sheet.uid(f"{self.ref}.{num}")]])
        node.append([Sym("instances"), [Sym("project"), project,
                                        [Sym("path"), f"/{root_uuid}", [Sym("reference"), self.ref],
                                         [Sym("unit"), self.unit]]]])
        return node


class Sheet:
    def __init__(self, name: str, title: str, paper: str = "A4", rev: str = "0.1", date: str = "",
                 comments: list[str] | None = None):
        self.name, self.title, self.paper, self.rev, self.date = name, title, paper, rev, date
        self.comments = comments or []
        self.uid = Uuid(name)
        self.root_uuid = self.uid("root")
        self.lib_symbols: dict[str, list] = {}
        self.parts: list[Part] = []
        self.wires: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self.labels: list[tuple[str, tuple[float, float], int, str]] = []
        self.texts: list[tuple[str, tuple[float, float], float, bool]] = []
        self.no_connects: list[tuple[float, float]] = []
        self.rects: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self._pwr = 0

    # ---- building -------------------------------------------------------
    def use_symbol(self, lib_id):
        if lib_id not in self.lib_symbols:
            self.lib_symbols[lib_id] = load_symbol(lib_id)
        return self.lib_symbols[lib_id]

    def add(self, lib_id, ref, value, at, **kw) -> Part:
        p = Part(self, lib_id, ref, value, at, **kw)
        self.parts.append(p)
        return p

    def power(self, kind: str, at, rot=0) -> Part:
        self._pwr += 1
        prefix = "#FLG" if kind == "PWR_FLAG" else "#PWR"
        val_at = {"GND": (0, 3.81, "center"), "PWR_FLAG": (0, -3.81, "center")}.get(kind, (0, -3.81, "center"))
        return self.add(f"power:{kind}", f"{prefix}{self._pwr:02d}", kind, at, rot=rot, val_at=val_at)

    def wire(self, *pts):
        pts = [(r(x), r(y)) for x, y in pts]
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise ValueError(f"non-orthogonal wire {a}->{b}")
            if a != b:
                self.wires.append((a, b))

    def label(self, name, at, rot=0, justify="left bottom"):
        self.labels.append((name, (r(at[0]), r(at[1])), rot, justify))

    def text(self, body, at, size=1.27, bold=False):
        self.texts.append((body, at, size, bold))

    def nc(self, at):
        self.no_connects.append((r(at[0]), r(at[1])))

    def rect(self, a, b):
        self.rects.append((a, b))

    # ---- connectivity ---------------------------------------------------
    def _pin_points(self):
        pts = {}
        for p in self.parts:
            for num in p.pins_local:
                pts.setdefault(p.pin(num), []).append((p, num))
        return pts

    def _split_wires(self):
        stops = set()
        for a, b in self.wires:
            stops.update([a, b])
        stops.update(self._pin_points())
        stops.update(at for _n, at, _r, _j in self.labels)
        changed = True
        while changed:
            changed = False
            out = []
            for a, b in self.wires:
                hit = None
                for s in stops:
                    if s in (a, b):
                        continue
                    if a[0] == b[0] == s[0] and min(a[1], b[1]) < s[1] < max(a[1], b[1]):
                        hit = s
                    elif a[1] == b[1] == s[1] and min(a[0], b[0]) < s[0] < max(a[0], b[0]):
                        hit = s
                    if hit:
                        break
                if hit:
                    out += [(a, hit), (hit, b)]
                    changed = True
                else:
                    out.append((a, b))
            self.wires = out

    def finalize(self, auto_nc=True):
        self._split_wires()
        ends: dict[tuple, int] = {}
        for a, b in self.wires:
            ends[a] = ends.get(a, 0) + 1
            ends[b] = ends.get(b, 0) + 1
        pins = self._pin_points()
        self.junctions = sorted(pt for pt, n in ends.items() if n + len(pins.get(pt, [])) >= 3 and n >= 2)
        if auto_nc:
            used = set(ends) | {at for _n, at, _r, _j in self.labels} | set(self.no_connects)
            for pt, owners in pins.items():
                if pt in used or len({id(o[0]) for o in owners}) > 1:
                    continue
                part, _num = owners[0]
                if not part.is_power:
                    self.no_connects.append(pt)

    # ---- output ---------------------------------------------------------
    def to_sexp(self):
        tb = [Sym("title_block"), [Sym("title"), self.title], [Sym("date"), self.date], [Sym("rev"), self.rev]]
        for i, c in enumerate(self.comments, 1):
            tb.append([Sym("comment"), i, c])
        root = [Sym("kicad_sch"), [Sym("version"), KICAD_SCH_VERSION], [Sym("generator"), "eeschema"],
                [Sym("generator_version"), "9.0"], [Sym("uuid"), self.root_uuid], [Sym("paper"), self.paper], tb,
                [Sym("lib_symbols")] + list(self.lib_symbols.values())]
        for pt in self.junctions:
            root.append([Sym("junction"), [Sym("at"), fmt(pt[0]), fmt(pt[1])], [Sym("diameter"), 0],
                         [Sym("color"), 0, 0, 0, 0], [Sym("uuid"), self.uid("j")]])
        for pt in self.no_connects:
            root.append([Sym("no_connect"), [Sym("at"), fmt(pt[0]), fmt(pt[1])], [Sym("uuid"), self.uid("nc")]])
        for a, b in self.wires:
            pts = [Sym("pts"), [Sym("xy"), fmt(a[0]), fmt(a[1])], [Sym("xy"), fmt(b[0]), fmt(b[1])]]
            stroke = [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]]
            root.append([Sym("wire"), pts, stroke, [Sym("uuid"), self.uid("w")]])
        for a, b in self.rects:
            root.append([Sym("rectangle"), [Sym("start"), fmt(a[0]), fmt(a[1])], [Sym("end"), fmt(b[0]), fmt(b[1])],
                         [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("dash")]],
                         [Sym("fill"), [Sym("type"), Sym("none")]], [Sym("uuid"), self.uid("rect")]])
        for body, at, size, bold in self.texts:
            root.append([Sym("text"), body, [Sym("exclude_from_sim"), Sym("no")],
                         [Sym("at"), fmt(at[0]), fmt(at[1]), 0], _effects(size, "left top", bold=bold),
                         [Sym("uuid"), self.uid("t")]])
        for name, at, rot, justify in self.labels:
            root.append([Sym("label"), name, [Sym("at"), fmt(at[0]), fmt(at[1]), rot],
                         [Sym("fields_autoplaced"), Sym("yes")], _effects(justify=justify),
                         [Sym("uuid"), self.uid("l")]])
        for p in self.parts:
            root.append(p.to_sexp(self.name, self.root_uuid))
        root.append([Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]])
        root.append([Sym("embedded_fonts"), Sym("no")])
        return root

    def write(self, directory):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.name}.kicad_sch")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(dump(self.to_sexp()) + "\n")
        pro = os.path.join(directory, f"{self.name}.kicad_pro")
        with open(pro, "w", encoding="utf-8") as fh:
            fh.write(PROJECT_TEMPLATE.replace("@NAME@", self.name))
        return path


PROJECT_TEMPLATE = """{
  "meta": {
    "filename": "@NAME@.kicad_pro",
    "version": 3
  },
  "schematic": {
    "legacy_lib_dir": "",
    "legacy_lib_list": []
  },
  "sheets": [],
  "text_variables": {}
}
"""


def parse_netlist(path: str) -> dict[str, set[str]]:
    """Return {net name: {"REF.pin", ...}} from a kicadsexpr netlist, power symbols excluded."""
    with open(path, encoding="utf-8") as fh:
        tree = parse(fh.read())
    nets = {}
    for net in find(first(tree, "nets"), "net"):
        name = first(net, "name")[1]
        nodes = {f"{first(n, 'ref')[1]}.{first(n, 'pin')[1]}" for n in find(net, "node")}
        nodes = {n for n in nodes if not n.startswith("#")}
        if nodes:
            nets[name] = nodes
    return nets
