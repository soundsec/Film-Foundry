"""Synchronize the packed shared collection; preserve ordinary phone catalogs."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from film_foundry.app.finder_resources import unpack, pack
from phone_preset_fields import allowed
source=ROOT/'film_foundry/assets/finder_resources.bin'
data=source.read_bytes();payload=unpack(data)
assert len(payload['phone_catalog']['materials'])==len(payload['phone_catalog']['scanners'])==8
destination=ROOT/'android/app/src/main/assets/finder_resources.bin'
# The PC payload may intentionally retain historical phone snapshots; project the latest
# desktop definitions through supported mobile fields instead of copying that stale branch.
for group,section in [('materials','film'),('scanners','scanner')]:
    for entry in payload['phone_catalog'][group]:
        source=payload['desktop'][section][entry['id']][section]
        entry['recipe'][section]={k:v for k,v in source.items() if k in allowed[section]}
        if section=='scanner':
            entry['recipe'][section].update(print_mapping_mode='sigmoid',scan_normalize_strength=1.0)
materials={e['id']:e['recipe']['film'] for e in payload['phone_catalog']['materials']}
scanners={e['id']:e['recipe']['scanner'] for e in payload['phone_catalog']['scanners']}
for pair in payload['phone_pairings']['entries']:
    pair['recipe']['film']=materials[pair['material_id']]
    pair['recipe']['scanner']=scanners[pair['id']+'_scan']
payload['phone_material_revision']='phone_reduced_materials_v3'
destination.write_bytes(pack(payload))
print('Synced the packed collection; ordinary catalogs and Smits resources untouched.')
