"""Synchronize the packed shared collection; preserve ordinary phone catalogs."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from film_foundry.app.finder_resources import unpack
source=ROOT/'film_foundry/assets/finder_resources.bin'
data=source.read_bytes();payload=unpack(data)
assert len(payload['phone_catalog']['materials'])==len(payload['phone_catalog']['scanners'])==8
destination=ROOT/'android/app/src/main/assets/finder_resources.bin'
destination.write_bytes(data)
print('Synced the packed collection; ordinary catalogs and Smits resources untouched.')
