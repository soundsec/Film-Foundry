"""Material-specific optical acquisition followed by the shared scanner controls.

Only display adjustments belong here. Reflection is never treated as a negative
density, and observation preferences are not stored in the formation state.
"""
from copy import deepcopy
import math
import numpy as np
from film_foundry.model.config import ScannerConfig,normalize_scanner_tone_curve,normalize_scanner_color_curve_rgb,normalize_color_mixer
from film_foundry.core.scanner import render_positive_transparency_scan,scan_normalization_range,apply_scan_normalization_range,encode_scan_output

DEFAULTS={'print_gamma':1.,'scan_saturation':1.,'print_color_bias':(1.,1.,1.),
          'print_color_shift':(0.,0.,0.),'highlight_color_bias':(1.,1.,1.),
          'highlight_bias_threshold':.72,'highlight_bias_softness':.18,
          'scan_normalize':False,'scan_black_percentile':.3,'scan_white_percentile':99.7,
          'scan_normalize_strength':.45,'scan_normalize_mode':'luma',
          'tone_curve':None,'color_curve_rgb':None,'color_mixer':None,'rotation_quarters':0}


def normalize_adjustments(value):
    if value is None:return {}
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('Unsupported material scan controls')
    data=deepcopy(value)
    turns=data.get('rotation_quarters',0)
    if type(turns) is not int or not 0<=turns<=3:raise ValueError('Invalid observation rotation')
    limits={'print_gamma':(.01,8),'scan_saturation':(0,4),'highlight_bias_threshold':(0,1),
            'highlight_bias_softness':(.001,1),'scan_black_percentile':(0,100),
            'scan_white_percentile':(0,100),'scan_normalize_strength':(0,1)}
    for key,(low,high) in limits.items():
        v=data.get(key,DEFAULTS[key])
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not low<=v<=high:raise ValueError('Invalid scan control: '+key)
    if data.get('scan_black_percentile',.3)>=data.get('scan_white_percentile',99.7):raise ValueError('Black percentile must be below white percentile')
    if type(data.get('scan_normalize',False)) is not bool:raise ValueError('Invalid scan normalize switch')
    if data.get('scan_normalize_mode','luma')!='luma':raise ValueError('Material auto endpoints use luminance, preserving colour preferences')
    for key,low,high in (('print_color_bias',0,4),('print_color_shift',-2,2),('highlight_color_bias',0,4)):
        if key in data:
            a=np.asarray(data[key],dtype=np.float64)
            if a.shape!=(3,) or not np.isfinite(a).all() or np.any((a<low)|(a>high)):raise ValueError('Invalid scan RGB control: '+key)
            data[key]=tuple(float(x) for x in a)
    for key,fn in (('tone_curve',normalize_scanner_tone_curve),('color_curve_rgb',normalize_scanner_color_curve_rgb),('color_mixer',normalize_color_mixer)):
        if key in data:data[key]=fn(data[key])
    return {k:v for k,v in data.items() if v!=DEFAULTS[k]}


def scanner_for_material(value=None):
    settings={**DEFAULTS,**normalize_adjustments(value)};settings.pop('rotation_quarters')
    return ScannerConfig(**settings,
        positive_scan_color_control_strength=1.,remove_base_mask=False,invert_transmission=False)


def render_material_scan(image,adjustments=None,*,border=(0,0,0,0)):
    values=normalize_adjustments(adjustments);turns=values.pop('rotation_quarters',0);scanner=scanner_for_material(values)
    from film_foundry.core.image_orientation import rotate_output
    if not values:
        from film_foundry.core.material_parallel import run_rows
        def encode(start,stop):
            image[start:stop]=encode_scan_output(image[start:stop],scanner,consume_input=True)
        run_rows(image.shape[0],image.shape[1],encode)
        return rotate_output(image,turns)
    image=render_positive_transparency_scan(image,scanner,consume_input=True)
    if scanner.scan_normalize and scanner.scan_normalize_strength>0:
        top,bottom,left,right=border
        sample=image[top:image.shape[0]-bottom,left:image.shape[1]-right]
        low,high=scan_normalization_range(sample,black_percentile=scanner.scan_black_percentile,
            white_percentile=scanner.scan_white_percentile,mode='luma')
        image=apply_scan_normalization_range(image,low,high,strength=scanner.scan_normalize_strength,consume_input=True)
    return rotate_output(encode_scan_output(image,scanner,consume_input=True),turns)
