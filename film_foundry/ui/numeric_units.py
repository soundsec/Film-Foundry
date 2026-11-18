"""Presentation suffixes only. Stored values, scales and ranges stay unchanged."""
from film_foundry.ui.curve_editor import text


def label_and_suffix(label):
    for ending,suffix in ((' mm/s',' mm/s'),(' seconds',' s'),(' °C',' °C'),(' min',' min'),
                          (' stop',text(' 档',' stops')),(' EV',' EV'),(' K',' K'),
                          (' C',' °C'),(' x',' ×'),(' %',' %'),(' mm',' mm'),(' s',' s'),(' °','°')):
        if label.endswith(ending):return label[:-len(ending)],suffix
    return label,''


def apply_unit(control,label):
    caption,suffix=label_and_suffix(label)
    if suffix:control.setSuffix(suffix)
    control.setAccessibleName(label)
    return caption
