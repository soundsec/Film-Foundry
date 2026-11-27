"""Capture decoding before material exposure; no formation or scan formulas.

SDR retains the established display-sRGB path. DNG and JPEG gain maps return
linear working RGB with an explicit encoding, never a tone-mapped exposure.
"""
from pathlib import Path
from contextlib import contextmanager
from functools import lru_cache
import io
import struct

import numpy as np
from PIL import Image


def _rawpy():
    try:
        import rawpy
        return rawpy
    except ImportError as exc:
        raise RuntimeError('DNG input requires rawpy (install project requirements).') from exc


@contextmanager
def _open_dng(path):
    import tifffile
    # LibRaw probes file contents and also accepts vendor RAW renamed to .dng.
    with tifffile.TiffFile(path) as container:
        if not container.pages or 50706 not in container.pages[0].tags:
            raise ValueError("Expected a DNGVersion tag in the primary DNG directory.")
    rawpy = _rawpy()
    try:
        with rawpy.imread(str(path)) as raw:
            yield raw
    except rawpy.LibRawError as exc:
        raise ValueError(f'LibRaw could not decode DNG {Path(path).name}: {exc}') from exc


def dng_dimensions(path):
    with _open_dng(path) as raw:
        size = raw.sizes
        w, h = size.width, size.height
        # LibRaw's transpose bit also covers the two mirrored quarter-turns.
        return (h, w) if size.flip & 4 else (w, h)


def _decode_dng16(path):
    from .computation_control import checkpoint
    checkpoint()
    rawpy = _rawpy()
    with _open_dng(path) as raw:
        # LibRaw owns the mosaic and interpolation. Asking rawpy to reconstruct
        # its pattern here would restrict decoding to patterns that wrapper knows.
        if raw.num_colors == 1:
            layout = 'single_plane'
        elif raw.raw_type == rawpy.RawType.Stack:
            layout = 'linear_planes'
        else:
            layout = 'cfa'
        checkpoint()
        # LibRaw applies black/white levels, camera WB, demosaic, color matrix
        # and orientation. No auto-brightening or display tone curve.
        pixels = raw.postprocess(output_color=rawpy.ColorSpace.sRGB,
            gamma=(1, 1), output_bps=16, use_camera_wb=True,
            use_auto_wb=False, no_auto_bright=True, bright=1.0)
        levels = {'black_level_per_channel': list(raw.black_level_per_channel),
                  'white_level': raw.white_level}
    checkpoint()
    if pixels.ndim == 3 and pixels.shape[-1] == 1:
        pixels = np.broadcast_to(pixels, (*pixels.shape[:2], 3))
    if pixels.ndim != 3 or pixels.shape[-1] != 3 or pixels.dtype != np.uint16:
        raise ValueError(f'Unsupported LibRaw output for {Path(path).name}: {pixels.shape}, {pixels.dtype}')
    return pixels, {
        'source': 'dng', 'capture_kind': 'sensor_raw_dng', 'raw_layout': layout,
        'decoder': f'rawpy {rawpy.__version__}',
        'libraw_version': '.'.join(map(str, rawpy.libraw_version)),
        'encoding': 'linear_rgb', 'working_space': 'linear_srgb',
        'white_balance': 'none' if layout == 'single_plane' else 'camera', 'auto_bright': False,
        'sensor_levels': levels,
        'range': 'sensor_normalized_16bit', 'orientation_applied': True,
    }


@lru_cache(maxsize=1)
def _display_codes():
    from .color import linear_to_srgb
    return linear_to_srgb(np.arange(65536, dtype=np.float32) / np.float32(65535))


def dng_display_image(path):
    # LibRaw's linear 16-bit output has exactly 65536 possible channel values.
    # Apply the same display transfer before thumbnail resampling.
    codes, record = _decode_dng16(path)
    return _display_codes()[codes], {**record, 'encoding': 'srgb', 'preview_only': True}


# ICC colorants are PCS D50. Identify the same three gamuts understood by
# libultrahdr; transfer curves in these profiles do not apply to decoded linear RGB.
_D50 = {
    'bt709': np.array([[.436075,.385065,.143080],[.222505,.716879,.060617],[.013932,.097105,.714173]]),
    'display_p3': np.array([[.515102,.291965,.157153],[.241182,.692236,.066581],[-.001049,.041882,.784378]]),
    'bt2020': np.array([[.673459,.165661,.125100],[.279033,.675338,.045629],[-.001931,.029979,.797162]]),
}
_TO_SRGB = {
    'bt709': np.eye(3, dtype=np.float32),
    'display_p3': np.array([[1.224940,-.224940,0],[-.042057,1.042057,0],[-.019638,-.078636,1.098274]], np.float32),
    'bt2020': np.array([[1.660491,-.587641,-.072850],[-.124550,1.132900,-.008349],[-.018151,-.100579,1.118730]], np.float32),
}


def _icc_gamut(profile):
    if not profile:
        return 'bt709'
    try:
        count = struct.unpack_from('>I', profile, 128)[0]
        tags = {}
        for i in range(count):
            tag, offset, size = struct.unpack_from('>4sII', profile, 132 + 12*i)
            if tag in (b'rXYZ', b'gXYZ', b'bXYZ'):
                if size < 20 or offset + size > len(profile) or profile[offset:offset+4] != b'XYZ ':
                    raise ValueError('Invalid ICC colorant')
                tags[tag] = np.array(struct.unpack_from('>iii', profile, offset+8)) / 65536
        matrix = np.column_stack([tags[k] for k in (b'rXYZ', b'gXYZ', b'bXYZ')])
        for name, expected in _D50.items():
            if np.max(np.abs(matrix - expected)) < .003:
                return name
    except (ValueError, KeyError, struct.error):
        pass
    raise ValueError('Ultra HDR color gamut is not supported (expected BT.709, Display P3 or BT.2020).')


def _jpeg_capture(path):
    # Read only JPEG headers for ordinary SDR files. MPF is used by Ultra HDR;
    # gain-map XMP/ISO markers also catch damaged/incomplete containers.
    with Image.open(path) as image:
        segments = getattr(image, 'applist', ())
        candidate = any(b'MPF\0' in data or b'hdr-gain-map' in data or
                        b'21496' in data or b'GainMap' in data for _, data in segments)
        if not candidate:
            return None
    try:
        import imagecodecs
    except ImportError as exc:
        raise RuntimeError('Gain-map JPEG input requires imagecodecs with libultrahdr.') from exc
    data = Path(path).read_bytes()
    if not imagecodecs.ultrahdr_check(data):
        # An ordinary MPO is not necessarily HDR; explicit gain-map metadata
        # must never silently fall back to its SDR base when malformed.
        if any(b'hdr-gain-map' in s or b'21496' in s or b'GainMap' in s for _, s in segments):
            raise ValueError('Invalid or unsupported HDR gain-map JPEG.')
        return None
    with Image.open(io.BytesIO(data)) as image:
        orientation = int(image.getexif().get(274, 1))
        profile = image.info.get('icc_profile')
        # libultrahdr returns gain-map gamut when declared; otherwise base gamut.
        if getattr(image, 'n_frames', 1) > 1:
            image.info.pop('icc_profile', None)
            image.seek(1)
            profile = image.info.get('icc_profile') or profile
        else:
            raise ValueError('Cannot inspect the HDR gain-map color profile in this JPEG container.')
        gamut = _icc_gamut(profile)
    pixels = imagecodecs.ultrahdr_decode(data, dtype=np.float16, gpu=False)[..., :3].astype(np.float32)
    if gamut != 'bt709':
        pixels = pixels @ _TO_SRGB[gamut].T
    np.maximum(pixels, 0, out=pixels)
    from .io_utils import _apply_exif_orientation_array
    pixels = np.ascontiguousarray(_apply_exif_orientation_array(pixels, orientation))
    if not np.isfinite(pixels).all():
        raise ValueError('HDR decoder returned non-finite exposure values.')
    return pixels, {'source': 'ultra_hdr_jpeg', 'decoder': imagecodecs.ultrahdr_version(),
        'encoding': 'linear_rgb', 'working_space': 'linear_srgb',
        'source_gamut': gamut, 'reference_white_nits': 203,
        'gain_map_applied': True, 'orientation_applied': True,
        'out_of_gamut': 'negative_components_clipped', 'highlight_clipping': False}


def load_formation_image(path, *, decode_long_edge=None, input_icc_policy='apply'):
    """Return pixels and an explicit `encoding` record for the shared pipeline."""
    path = Path(path)
    if decode_long_edge is not None and int(decode_long_edge) <= 0:
        raise ValueError('decode_long_edge must be positive or None.')
    suffix = path.suffix.lower()
    if suffix == '.dng':
        codes, record = _decode_dng16(path)
        linear = codes.astype(np.float32)
        linear /= np.float32(65535)
        return linear, record
    if suffix in ('.jpg', '.jpeg'):
        decoded = _jpeg_capture(path)
        if decoded is not None:
            return decoded
    from .io_utils import load_image_with_color
    image, record = load_image_with_color(path, decode_long_edge=decode_long_edge,
                                         input_icc_policy=input_icc_policy)
    if suffix in ('.jpg', '.jpeg'):
        record = {**record, 'capture_kind': 'display_referred_srgb'}
    return image, {**record, 'encoding': 'srgb'}


def exposure_preview(image, encoding):
    """Display-only mapping; never used to feed formation."""
    if encoding == 'srgb':
        return image
    from .color import linear_to_srgb
    peak = float(np.max(image))
    return linear_to_srgb(image / max(1., peak))
