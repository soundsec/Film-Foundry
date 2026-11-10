"""Capture decoding before material exposure; no formation or scan formulas.

SDR retains the established display-sRGB path. DNG and JPEG gain maps return
linear working RGB with an explicit encoding, never a tone-mapped exposure.
"""
from pathlib import Path
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


def dng_dimensions(path):
    with _rawpy().imread(str(path)) as raw:
        size = raw.sizes
        w, h = size.width, size.height
        return (h, w) if size.flip in (5, 6) else (w, h)


def _dng(path):
    rawpy = _rawpy()
    with rawpy.imread(str(path)) as raw:
        # LibRaw applies black/white levels, camera WB, demosaic, color matrix
        # and orientation. No auto-brightening or display tone curve.
        pixels = raw.postprocess(output_color=rawpy.ColorSpace.sRGB,
            gamma=(1, 1), output_bps=16, use_camera_wb=True,
            use_auto_wb=False, no_auto_bright=True, bright=1.0)
    return pixels.astype(np.float32) / np.float32(65535), {
        'source': 'dng', 'decoder': f'rawpy {rawpy.__version__}',
        'encoding': 'linear_rgb', 'working_space': 'linear_srgb',
        'white_balance': 'camera', 'auto_bright': False,
        'range': 'sensor_normalized_16bit', 'orientation_applied': True,
    }


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
        return _dng(path)
    if suffix in ('.jpg', '.jpeg'):
        decoded = _jpeg_capture(path)
        if decoded is not None:
            return decoded
    from .io_utils import load_image_with_color
    image, record = load_image_with_color(path, decode_long_edge=decode_long_edge,
                                         input_icc_policy=input_icc_policy)
    return image, {**record, 'encoding': 'srgb'}


def exposure_preview(image, encoding):
    """Display-only mapping; never used to feed formation."""
    if encoding == 'srgb':
        return image
    from .color import linear_to_srgb
    peak = float(np.max(image))
    return linear_to_srgb(image / max(1., peak))
