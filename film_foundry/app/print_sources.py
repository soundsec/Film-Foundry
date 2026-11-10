"""Read-only full-working-resolution sources for print delivery and creative plates."""
from pathlib import Path
from dataclasses import asdict
import numpy as np
from film_foundry.core.material_family import ViewingConditions


def body_components(body,view=None,output_shape=None):
    components = {}
    for key, value in body.fields.items():
        if value.ndim == 2:components[key] = value
        elif value.ndim == 3:
            for channel in range(value.shape[-1]):components[f'{key}[{channel}]'] = value[..., channel]
    if view is not None and output_shape is not None:
        from film_foundry.core.material_family import observation_border
        for key,value in list(components.items()):
            top,bottom,left,right=observation_border(body,value.shape,view)
            if any((top,bottom,left,right)) and (value.shape[0]+top+bottom,value.shape[1]+left+right)==tuple(output_shape[:2]):
                padded=np.pad(value,((top,bottom),(left,right)))
                padded.setflags(write=False);components[key]=padded
    return components


def load_print_source(asset, *, film_config=None, view=None):
    path = Path(asset['path'])
    kind, family = asset['kind'], asset.get('family')
    provenance = {'path': str(path.resolve()), 'name': path.name, 'family': family,
                  'body_id': asset.get('body_id')}
    components = {}
    if kind == 'medium' and family == 'film' or kind == 'scanner_raw':
        from film_foundry.app.workflow import observe_medium_file, load_developed_medium
        from film_foundry.model.config import DarkroomConfig
        from film_foundry.core.engine import scan_medium_output
        config = film_config or DarkroomConfig()
        if path.suffix.lower() == '.npz':
            medium = load_developed_medium(path)
            scanned = scan_medium_output(medium, config, config.scanner.interpretation_mode or 'auto')
            for field in ('density_cmy', 'density_grain', 'optical_density_rgb'):
                value = getattr(medium, field, None)
                if value is not None and value.size:
                    value.setflags(write=False)
                    for channel in range(value.shape[-1]):components[f'{field}[{channel}]'] = value[..., channel]
        else:scanned, *_ = observe_medium_file(path, config)
        image = scanned.output_srgb
        provenance['scanner'] = asdict(config.scanner)
    elif kind in ('material', 'auxiliary'):
        from film_foundry.app.material_lab_session import MaterialLabSession
        session = MaterialLabSession(family)
        session.load(path)
        matches = [i for i, body in enumerate(session.group.bodies)
                   if not asset.get('body_id') or body.body_id == asset['body_id']]
        if len(matches) != 1:raise ValueError('Select an individual material body for printing')
        index = matches[0];body = session.group.bodies[index]
        view = view or ViewingConditions()
        image = session.preview(view, index)
        components = body_components(body,view,image.shape)
        provenance.update(group_id=body.group_id, body_id=body.body_id, view=asdict(view))
    elif kind in ('source', 'scan', 'preview'):
        from film_foundry.core.io_utils import load_image
        image = load_image(path)
    else:raise ValueError('Select an image or a final individual medium')
    return {'image': image, 'provenance': provenance, 'components': components}


def print_result_record(path, provenance, *, artifacts=None):
    from film_foundry.core.atomic_io import atomic_write_json
    path = Path(path).resolve()
    source = provenance.get('path', '')
    record = {'key': 'print:' + str(path), 'source': source, 'label': path.name,
              'family': provenance.get('family') or 'printmaking', 'command': 'print',
              'medium': str(path), 'cover': str(path), 'artifacts': {'scan:print': str(path), **(artifacts or {})}}
    manifest = path.with_name(path.name + '.result.json')
    record['artifacts']['result'] = str(manifest)
    atomic_write_json(manifest, record)
    return record
