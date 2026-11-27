"""Expose individual factory marks into their registered horizontal slots."""
from functools import lru_cache
from importlib.resources import files

import cv2
import numpy as np

from film_foundry.contracts.edge_marks import EdgeMarkPattern, factory_edge_pattern
from film_foundry.contracts.exposure_glyphs import ROWS
from film_foundry.core.edge_direction import advance_stencil


@lru_cache(maxsize=2)
def _typeface(resource):
    data = files("film_foundry").joinpath("assets", resource).read_text("ascii")
    glyphs = {}
    for row in data.splitlines():
        if row.startswith("#"):
            continue
        code, width, height, bits = row.split()
        image = np.frombuffer(bytes.fromhex(bits), np.uint8).reshape(int(height), -1)
        glyphs[chr(int(code))] = np.unpackbits(image, axis=1)[:, :int(width)]
    return glyphs


def _lettering(label, font):
    if font in ("sans", "rounded"):
        glyphs = _typeface("edge_rounded.txt" if font == "rounded" else "edge_sans.txt")
        tiles = [glyphs.get(char, glyphs[" "]) for char in label]
        if font == "sans":
            return np.concatenate(tiles, axis=1)
        space = 2
    else:
        tiles = [(np.array(ROWS.get(ord(char), (0,) * 7))[:, None] >> np.arange(4, -1, -1)) & 1
                 for char in label]
        space = 1
    return np.concatenate([np.pad(tile, ((0, 0), (0, space))) for tile in tiles], axis=1)[:, :-space]


def _grid(pattern):
    return np.unpackbits(np.frombuffer(pattern.bits, np.uint8))[:pattern.width * pattern.height].reshape(pattern.height, pattern.width)


def expose_slots(dose, config, spec, label, index, top, height):
    if height <= 0:
        return
    width = dose.shape[1]
    for kind, start, end in config.factory_composition[spec.side]:
        left, right = round(start * width / 100), round(end * width / 100)
        span = right - left
        if span <= 0:
            continue
        if kind == "barcode":
            if config.factory_barcode == "none":
                continue
            mask = _grid(factory_edge_pattern("FF", config.factory_batch, index, "text_code",
                                               config.factory_barcode, "bottom", config.factory_barcode_seed))
        elif kind == "direction":
            if config.advance_marker_style == "none":
                continue
            style = "arrow" if config.advance_marker_style == "legacy" else config.advance_marker_style
            mask = advance_stencil(None, style, config.advance_direction, 64, 64)
        elif kind == "text" and spec.pattern_code:
            mask = _grid(EdgeMarkPattern.decode(spec.pattern_code))
        else:
            if kind == "number" and config.frame_number is None:
                continue
            mask = _lettering(label if kind == "text" else str(config.frame_number), config.factory_composition["font"])
        if spec.flip:
            mask = mask[::-1, ::-1]
        if kind == "barcode":
            w, h = span, height
        else:
            scale = min(span / mask.shape[1], height / mask.shape[0])
            w, h = max(1, round(mask.shape[1] * scale)), max(1, round(mask.shape[0] * scale))
        y = top + (height - h) // 2
        dose[y:y+h, left:left+w] = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA)
