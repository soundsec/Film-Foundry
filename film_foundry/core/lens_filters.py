"""Source-side filter attenuation, before emulsion optics, fog or camera light leaks."""
import numpy as np

from film_foundry.contracts.lens_filters import TRANSMISSION
from film_foundry.core.spectral_exposure import map_layer_exposure
from film_foundry.model.config import MONO_LEGACY_EXPOSURE


def _transmission(setting):
    if isinstance(setting, dict):
        if setting["key"] == "custom":
            return np.asarray(setting["transmission"], dtype=np.float32)
        setting = setting["key"]
    return np.asarray(TRANSMISSION[setting], dtype=np.float32)


def filter_loss_ev(film, key, side="emulsion"):
    throughput = _transmission(key)
    if film.physical_coating_optics:
        from .film_process.coatings import optical_path, received_light
        from .film_process.integration import _is_monochrome_material
        from film_foundry.contracts.coating_stack import RECORDS
        count = 1 if _is_monochrome_material(film) else 3
        samples = np.stack((np.ones(3, np.float32), throughput))
        if side == "base":
            from .rear_exposure import transmit_from_base
            transmit_from_base(samples, film, monochrome=count == 1)
        doses = np.zeros(2)
        for row, gain in optical_path(film, side):
            signal = received_light(samples * gain, film, row, count, RECORDS.index(row["record"]))
            doses += np.maximum(0., signal) * (row["amount"] * 2.**row["speed_ev"])
        transmission = doses[1] / doses[0] if doses[0] > 0 else 1.
    elif (film.spectral_exposure_profile is None and film.monochrome_exposure_model == MONO_LEGACY_EXPOSURE
            and film.color_process.strip().lower().replace("-", "_").replace(" ", "_") in {"bw", "black_white", "monochrome"}):
        transmission = np.dot(throughput, (.2126, .7152, .0722))
    else:
        # Compare neutral-light doses using the current stock. For colour film
        # this meter uses the sum of the receptor doses, preserving colour shifts.
        white, filtered = map_layer_exposure(np.stack((np.ones(3, np.float32), throughput)), film)
        transmission = np.maximum(filtered, 0).sum() / max(float(np.maximum(white, 0).sum()), 1e-12)
    return -float(np.log2(transmission)) if transmission > 0 else float("inf")


def filter_gain(film, setting, side="emulsion"):
    gain = _transmission(setting)
    if setting["compensate"]:
        loss = filter_loss_ev(film, setting, side)
        if not np.isfinite(loss):
            raise ValueError("No light reaches this stock through the filter; disable exposure compensation")
        gain = gain * 2.0 ** loss
    return gain
