"""Optional video decode before exposure; no encoding, editing, or film formulas."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np

from film_foundry.core.color import linear_to_srgb


@dataclass(frozen=True)
class VideoFrameInput:
    rgb: np.ndarray
    pts: int
    time_base_num: int
    time_base_den: int
    duration: int | None


def decode_video(source: Path):
    try:
        import av
    except ImportError as exc:
        raise ValueError('Video import requires film-foundry[video].') from exc
    with av.open(str(source)) as container:
        if len(container.streams.video) != 1:
            raise ValueError('Select a source with exactly one video stream.')
        stream = container.streams.video[0]
        if stream.sample_aspect_ratio not in (None,1):
            raise ValueError('Non-square video pixels require explicit input conversion.')
        context = stream.codec_context
        transfer = int(context.color_trc)
        if int(context.color_primaries) not in {1,2} or transfer not in {1,2,6,8,13}:
            raise ValueError('Movie ingest currently supports SDR BT.709/sRGB; HDR and wide-gamut need explicit color ingest.')
        for frame in container.decode(video=0):
            if frame.pts is None or frame.time_base is None:
                raise ValueError('Video frame has no presentation timestamp.')
            matrix = int(frame.colorspace)
            if matrix not in {1,2,5,6}:
                raise ValueError('Unsupported video YUV color matrix.')
            space = 'ITU601' if matrix in {5,6} else 'ITU709'
            pixels = frame.reformat(format='rgb48le',src_colorspace=space,dst_colorspace='ITU709').to_ndarray()
            rgb = pixels.astype(np.float32)/np.float32(65535)
            if transfer in {1,2,6}:
                linear = np.where(rgb < .081,rgb/4.5,((rgb+.099)/1.099)**(1/.45)).astype(np.float32)
                rgb = linear_to_srgb(linear)
            elif transfer == 8:
                rgb = linear_to_srgb(rgb)
            rotation = int(frame.rotation)%360
            if rotation not in {0,90,180,270}:
                raise ValueError('Movie rotation must be a multiple of 90 degrees.')
            if rotation:
                rgb = np.rot90(rgb,k=rotation//90)
            yield VideoFrameInput(rgb,int(frame.pts),frame.time_base.numerator,frame.time_base.denominator,
                                  int(frame.duration) if frame.duration else None)
