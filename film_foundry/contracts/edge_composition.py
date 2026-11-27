"""Registered factory plate. Slot limits are percentages of one frame pitch."""
from math import isfinite

KINDS = ("text", "number", "direction", "barcode")
DEFAULT_PLATE = {
    "font": "grid",
    "top": [["text", 4, 76]],
    "bottom": [["barcode", 4, 68], ["number", 78, 89], ["direction", 92, 99]],
}


def normalize_composition(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"font", "top", "bottom"}:
        raise ValueError("Expected font, top and bottom in factory_composition")
    if value["font"] not in ("grid", "sans", "rounded"):
        raise ValueError("Unknown factory lettering font")
    plate = {"font": value["font"]}
    for side in ("top", "bottom"):
        slots = value[side]
        if not isinstance(slots, (list, tuple)) or len(slots) > len(KINDS):
            raise ValueError("Invalid factory plate slots")
        row = []
        seen = set()
        for item in slots:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValueError("Expected [kind, start, end] on factory plate")
            kind, start, end = item
            if kind not in KINDS or kind in seen:
                raise ValueError("Unknown or duplicate factory mark")
            if any(type(v) not in (int, float) or not isfinite(v) for v in (start, end)) or not 0 <= start < end <= 100:
                raise ValueError("Factory mark must fit inside 0–100%")
            seen.add(kind)
            row.append([kind, start, end])
        row.sort(key=lambda slot: slot[1])
        if any(a[2] > b[1] for a, b in zip(row, row[1:])):
            raise ValueError("Factory mark slots overlap")
        plate[side] = row
    return plate
