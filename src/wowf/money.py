"""Copper <-> gold formatting."""

from __future__ import annotations


def fmt(copper: int | float | None, rich: bool = True) -> str:
    """1234567 -> '123g 45s 67c'. Zero-valued leading units are dropped."""
    if copper is None:
        return "-"
    c = int(round(copper))
    neg = c < 0
    c = abs(c)
    g, rem = divmod(c, 10000)
    s, cc = divmod(rem, 100)
    parts = []
    if g:
        parts.append(f"[gold1]{g:,}g[/]" if rich else f"{g:,}g")
    if s:
        parts.append(f"[grey82]{s}s[/]" if rich else f"{s}s")
    if cc or not parts:
        parts.append(f"[dark_orange3]{cc}c[/]" if rich else f"{cc}c")
    out = " ".join(parts)
    return ("-" + out) if neg else out


def parse(text: str) -> int:
    """'1g 50s', '75s', '20g', '30c', '150' (copper) -> copper."""
    t = text.lower().replace(" ", "")
    if t.isdigit():
        return int(t)
    import re

    m = re.fullmatch(r"(?:(\d+)g)?(?:(\d+)s)?(?:(\d+)c)?", t)
    if not m or not any(m.groups()):
        raise ValueError(f"can't read {text!r} as money (try 20g, 1g50s, 75s)")
    g, s, c = (int(x or 0) for x in m.groups())
    return g * 10000 + s * 100 + c
