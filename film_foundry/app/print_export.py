"""Print delivery of a fixed sRGB observation, independent of material formation."""
from pathlib import Path
from io import BytesIO
import math
import numpy as np
from PIL import Image, ImageCms
import tifffile
from film_foundry.core.atomic_io import atomic_output_path, atomic_path_set, atomic_write_json


def export_print(image, path, *, dpi=300, cmyk_profile=None, intent=1, metadata=None, protected_paths=()):
    path=Path(path);sidecar=path.with_suffix(path.suffix+'.json')
    if path.suffix.lower() not in ('.tif','.tiff'):raise ValueError('Print output requires TIFF')
    if not math.isfinite(dpi) or not 36<=dpi<=2400:raise ValueError('DPI must be 36–2400')
    if intent not in (0,1,2,3):raise ValueError('Invalid ICC rendering intent')
    if any(p and Path(p).resolve() in (path.resolve(),sidecar.resolve()) for p in protected_paths):raise ValueError('Output must not overwrite its source')
    a=np.asarray(image,dtype=np.float32)
    if a.ndim!=3 or a.shape[-1]!=3 or not a.size or not np.isfinite(a).all():raise ValueError('Expected finite sRGB image')
    srgb=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
    if cmyk_profile:
        icc=Path(cmyk_profile).read_bytes()
        profile=ImageCms.ImageCmsProfile(BytesIO(icc))
        if profile.profile.xcolor_space.strip()!='CMYK':raise ValueError('Choose the printer/paper CMYK ICC profile')
        rgb=Image.fromarray(np.uint8(np.clip(a,0,1)*255+.5))
        output=ImageCms.profileToProfile(rgb,srgb,profile,outputMode='CMYK',renderingIntent=intent)
        space='CMYK';depth=8
    else:
        output=np.uint16(np.clip(a,0,1)*65535+.5);space='sRGB';depth=16;icc=srgb.tobytes()
    record={'model':'print_delivery_v1','color_space':space,'bit_depth':depth,'dpi':dpi,
            'pixel_size':list(a.shape[1::-1]),'size_mm':[a.shape[1]*25.4/dpi,a.shape[0]*25.4/dpi],
            'icc_profile':Path(cmyk_profile).name if cmyk_profile else 'sRGB', 'rendering_intent':intent,
            'resampling':'none','source':metadata or {}}
    with atomic_path_set((path,sidecar)):
        with atomic_output_path(path) as tmp:
            if cmyk_profile:output.save(tmp,format='TIFF',compression='tiff_lzw',dpi=(dpi,dpi),icc_profile=icc)
            else:tifffile.imwrite(tmp,output,photometric='rgb',compression='deflate',resolution=(dpi,dpi),resolutionunit='INCH',metadata=None,extratags=[(34675,'B',len(icc),icc,False)])
        atomic_write_json(sidecar,record)
    return record
