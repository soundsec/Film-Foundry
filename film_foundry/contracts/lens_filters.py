"""Visible-light lens filters. Values are relative linear working-RGB transmission."""

MODEL = "visible_rgb_lens_filter_v1"
TRANSMISSION = {
    "yellow": (.94, .88, .18),
    "orange": (.94, .47, .06),
    "red": (.90, .045, .012),
    "green": (.055, .80, .09),
    "blue": (.018, .085, .78),
    "skylight": (.99, .965, .90),
}


def lens_filter(key, compensate=False, transmission=None):
    if key == "custom" and type(compensate) is bool:
        if (not isinstance(transmission, (list, tuple)) or len(transmission) != 3 or
            any(type(v) not in (int, float) or not 0 <= v <= 1 for v in transmission)):
            raise ValueError("Custom lens transmission requires three fractions from 0 to 1")
        if compensate and not any(transmission):
            raise ValueError("An opaque filter cannot be exposure-compensated")
        return dict(model=MODEL, key=key, compensate=compensate, transmission=list(transmission))
    if not isinstance(key, str) or key not in TRANSMISSION or type(compensate) is not bool:
        raise ValueError("Invalid visible lens filter or exposure compensation")
    return {"model": MODEL, "key": key, "compensate": compensate}


def validate_filter(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Unsupported lens filter")
    fields = {"model", "key", "compensate"}
    if value.get("key") == "custom":
        fields.add("transmission")
    if set(value) != fields or value["model"] != MODEL:
        raise ValueError("Unsupported lens filter")
    return lens_filter(value["key"], value["compensate"], value.get("transmission"))
