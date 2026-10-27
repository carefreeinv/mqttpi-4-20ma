"""Minimal S-expression reader/writer for KiCad files (no third-party deps)."""

from __future__ import annotations

import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')


class Sym(str):
    """An unquoted atom."""


def parse(text: str):
    stack: list[list] = [[]]
    pos = 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        lp, rp, qs, atom = m.groups()
        if lp:
            stack.append([])
        elif rp:
            done = stack.pop()
            stack[-1].append(done)
        elif qs is not None:
            stack[-1].append(qs[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        elif atom is not None:
            stack[-1].append(Sym(atom))
    return stack[0][0]


def dump(node, indent: int = 0) -> str:
    if isinstance(node, list):
        simple = all(not isinstance(x, list) for x in node)
        if simple:
            return "(" + " ".join(dump(x) for x in node) + ")"
        pad = "\t" * (indent + 1)
        parts = []
        for x in node:
            parts.append(("\n" + pad + dump(x, indent + 1)) if isinstance(x, list) else dump(x))
        head = []
        rest = []
        for i, x in enumerate(node):
            if isinstance(x, list):
                rest = node[i:]
                break
            head.append(x)
        out = "(" + " ".join(dump(x) for x in head)
        for x in rest:
            out += "\n" + pad + (dump(x, indent + 1) if isinstance(x, list) else dump(x))
        return out + "\n" + "\t" * indent + ")"
    if isinstance(node, Sym):
        return str(node)
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int, float)):
        return f"{node:g}" if isinstance(node, float) else str(node)
    s = str(node).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None
