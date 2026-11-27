"""Front-to-support coating order and quantities in units of one stock record."""
from copy import deepcopy
from math import isfinite

MODEL = "ordered_coatings_v1"
RECORDS = ("R", "G", "B")


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a finite number in [{minimum}, {maximum}]")
    return float(value)


def _rgb(value, name):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{name} requires three RGB values")
    return [_number(v, name, 0., 32.) for v in value]


def validate_stack(stack):
    if stack is None:
        return None
    if not isinstance(stack, dict) or stack.get("model") != MODEL or not isinstance(stack.get("layers"), (tuple, list)):
        raise ValueError("Invalid ordered coating stack")
    if stack.keys() - {"model", "mode", "layers", "exposure_basis", "independent_grain"} or stack.get("mode", "ordered") not in ("ordered", "reduced"):
        raise ValueError("Invalid coating model or mode")
    if type(stack.get("independent_grain", False)) is not bool:
        raise ValueError("independent_grain must be true or false")
    basis = stack.get("exposure_basis", "physical")
    if basis not in ("stock", "physical"):
        raise ValueError("Unknown exposure basis for film coatings")
    rows = []
    ids = set()
    for raw in stack["layers"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid coating row")
        key = raw.get("id")
        if not isinstance(key, str) or not key.strip() or key in ids:
            raise ValueError("Each coating needs a unique, non-empty ID")
        ids.add(key)
        kind = raw.get("kind")
        row = dict(id=key, kind=kind, density_rgb=_rgb(raw.get("density_rgb", [0., 0., 0.]), "density_rgb"))
        if kind == "emulsion":
            record = raw.get("record")
            if record not in RECORDS:
                raise ValueError("Emulsion record must be R, G or B")
            row.update(record=record, speed_ev=_number(raw.get("speed_ev", 0.), "speed_ev", -24., 24.),
                       amount=_number(raw.get("amount", 1.), "amount", 0.0001, 32.),
                       development_rate=_number(raw.get("development_rate", 1.), "development_rate", 0., 8.),
                       coupler_fraction=_number(raw.get("coupler_fraction", 1.), "coupler_fraction", 0., 1.))
            if raw.get("sensitivity_rgb") is not None:
                row["sensitivity_rgb"] = _rgb(raw["sensitivity_rgb"], "sensitivity_rgb")
            if "band_nm" in raw:
                band = raw["band_nm"]
                if (not isinstance(band, (list, tuple)) or len(band) != 2 or
                    any(type(n) is not int or not 380 <= n <= 780 or n % 5 for n in band) or band[0] >= band[1]):
                    raise ValueError("Use a visible band from 380 to 780 nm in 5 nm steps")
                if basis != "physical" or "sensitivity_rgb" in row:
                    raise ValueError("A wavelength band requires physical optics and replaces RGB sensitivity")
                row["band_nm"] = list(band)
        elif kind == "support":
            if basis != "physical" or any(row["density_rgb"]) or any(r["kind"] == "support" for r in rows):
                raise ValueError("Place one stock support in the physical path; its density comes from the material")
        elif kind != "filter":
            raise ValueError("Unknown coating kind")
        if "spectrum" in raw:
            if kind == "support" or basis != "physical" or "band_nm" in row:
                raise ValueError("A coating spectrum needs physical optics and replaces the passband")
            row["spectrum"] = validate_curve(raw["spectrum"])
        unknown = raw.keys() - row.keys()
        if unknown:
            raise ValueError(f"Unknown coating fields: {sorted(unknown)}")
        rows.append(row)
    if not any(row["kind"] == "emulsion" for row in rows):
        raise ValueError("The stack needs at least one photosensitive coating")
    result = dict(model=MODEL, layers=rows)
    if "mode" in stack:
        result["mode"] = stack["mode"]
    if stack.get("independent_grain"):
        result["independent_grain"] = True
    if basis == "stock":
        result["exposure_basis"] = basis
    return result


def validate_curve(points):
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= 81:
        raise ValueError("Use 2–81 wavelength points")
    curve = []
    for pair in points:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each spectral point needs a wavelength and a fraction")
        nm = _number(pair[0], "wavelength", 380, 780)
        if curve and nm <= curve[-1][0]:
            raise ValueError("Wavelengths must increase")
        curve.append([nm, _number(pair[1], "response / transmission", 0, 1)])
    if curve[0][0] != 380 or curve[-1][0] != 780:
        raise ValueError("Include the 380 nm and 780 nm endpoints")
    return curve


def has_layer_separation(stack, *, silver_only=False):
    """Inspect validated rows, independent of the selected execution mode."""
    if not stack:
        return False
    layers = stack["layers"]
    if any(row["kind"] == "filter" and (any(row.get("density_rgb", ())) or
           any(v < 1 for _, v in row.get("spectrum", ()))) for row in layers):
        return True
    receptors = [row for row in layers if row["kind"] == "emulsion"]
    if len(receptors) < 2:
        return False
    if any(row["kind"] == "support" for row in layers):
        position = next(i for i, row in enumerate(layers) if row["kind"] == "support")
        if any(r["kind"] == "emulsion" for r in layers[:position]) and any(r["kind"] == "emulsion" for r in layers[position+1:]):
            return True
    if any(any(row.get("density_rgb", ())) for row in receptors):
        return True
    responses = {(row.get("speed_ev", 0.), row.get("development_rate", 1.), None if silver_only else row.get("coupler_fraction", 1.),
                  tuple(row.get("sensitivity_rgb", ())), tuple(row.get("band_nm", ())),
                  tuple(map(tuple, row.get("spectrum", ())))) for row in receptors}
    return len(responses) > 1


def split_emulsion(layers, index):
    """Bisect one physical coating, retaining its ID on the first half."""
    row = layers[index]
    if row["kind"] != "emulsion" or row["amount"] < .0002:
        raise ValueError("Select an emulsion with enough coating material to divide")
    half = deepcopy(row)
    half["amount"] *= .5
    half["density_rgb"] = [d*.5 for d in row["density_rgb"]]
    other = deepcopy(half)
    used = {r["id"] for r in layers}
    serial = 2
    while f'{row["id"]}-{serial}' in used:
        serial += 1
    other["id"] = f'{row["id"]}-{serial}'
    return half, other


def example_stack(monochrome=False):
    layers = []
    for record in (("G",) if monochrome else ("B", "G", "R")):
        for name, ev, amount in (("fast", .8, .4), ("slow", -.55, .6)):
            label = "Ag" if monochrome else record
            layers.append(dict(id=f"{label}-{name}", kind="emulsion", record=record,
                               speed_ev=ev, amount=amount, density_rgb=[.025, .025, .04]))
        if record == "B":
            layers.append(dict(id="yellow-filter", kind="filter", density_rgb=[.015, .025, .85]))
    return validate_stack(dict(model=MODEL, layers=layers))
