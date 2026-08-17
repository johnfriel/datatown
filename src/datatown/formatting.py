"""Small formatting helpers shared by operator reports."""

from __future__ import annotations


def format_bytes(byte_count: int) -> str:
    """Format a non-negative byte count using binary units."""
    if byte_count < 0:
        raise ValueError("byte_count must not be negative")
    value = float(byte_count)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
