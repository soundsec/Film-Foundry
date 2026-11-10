"""Desktop source catalog. Annotations never modify images or material state.

Rating semantics match Android GalleryPreferences / GalleryQuery: 0 is unrated,
1..5 stars, a separate favorite flag, and any/gt/lt/ge/le comparisons. Storage
is desktop-local; this is not a phone SharedPreferences synchronization format.
"""
from pathlib import Path
import os
import zipfile
from uuid import uuid4
from copy import deepcopy
from time import time

from film_foundry.app.paths import app_root
from film_foundry.core.atomic_io import atomic_write_json, strict_json_load

from film_foundry.core.io_utils import SUPPORTED_EXTENSIONS
from film_foundry.app.catalog_assets import path_key, record_assets, asset_descriptor, import_record
IMAGE_SUFFIXES = SUPPORTED_EXTENSIONS


class DesktopCatalog:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('FILM_FOUNDRY_CATALOG', app_root()/'user_presets'/'desktop_catalog.json'))
        self.entries = []
        self.results = []
        self.asset_annotations = {}
        self.hidden_paths = set()
        if self.path.exists():
            payload = strict_json_load(self.path)
            if payload.get('version') != 1 or not isinstance(payload.get('entries'), list):
                raise ValueError('Unsupported desktop catalog')
            for item in payload['entries']:
                if not isinstance(item.get('path'), str) or not isinstance(item.get('id'), str):
                    raise ValueError('Invalid catalog entry')
                if type(item.get('rating')) is not int or not 0 <= item['rating'] <= 5 or type(item.get('favorite')) is not bool:
                    raise ValueError('Invalid catalog annotation')
            self.entries = payload['entries']
            self.results = payload.get('results', [])
            self.asset_annotations = payload.get('asset_annotations', {})
            self.hidden_paths = set(payload.get('hidden_paths', []))
            for stack in self.results:
                for run in stack['runs']:
                    if 'assets' not in run:
                        try:run['assets']=record_assets(run,inspect=True)
                        except (OSError,ValueError,KeyError,zipfile.BadZipFile):run['assets']=record_assets(run)
            self._merge_lineage()

    def save(self):
        atomic_write_json(self.path, {'version': 1, 'entries': self.entries, 'results':self.results,
                                    'asset_annotations':self.asset_annotations, 'hidden_paths':sorted(self.hidden_paths)})

    def remove_items(self, items):
        """Hide references only. Retain lineage/annotations and never unlink user files."""
        previous = set(self.hidden_paths)
        for item in items:
            if 'runs' in item:
                self.hidden_paths.update(path_key(a['path']) for a in self.assets(item))
            else:
                self.hidden_paths.add(path_key(item['path']))
        try:self.save()
        except Exception:self.hidden_paths = previous;raise

    def index_candidates(self, folders):
        """Filesystem enumeration is run by the frontend's background worker."""
        paths = []
        seen = {path_key(e['path']) for e in self.entries} | self.hidden_paths
        for folder in folders:
            for directory, _, files in os.walk(folder, followlinks=False):
                for name in files:
                    path = Path(directory)/name
                    key = path_key(path)
                    if path.suffix.lower() in IMAGE_SUFFIXES and key not in seen:
                        paths.append(str(path));seen.add(key)
        return paths

    def _merge_lineage(self):
        """Join actual parent files, even when children were imported first. No filename guessing."""
        changed=True
        while changed:
            changed=False
            owners={}
            for group in self.results:
                for run in group['runs']:
                    for path in (run['medium'],*run.get('artifacts',{}).values()):
                        if path:owners.setdefault(path_key(path),group)
            for stack in list(self.results):
                parent=next((owners.get(path_key(run.get('source',''))) for run in stack['runs']
                             if owners.get(path_key(run.get('source',''))) not in (None,stack)),None)
                if parent is None:continue
                for run in stack['runs']:
                    if stack['rating'] or stack['favorite']:
                        for asset in run.get('assets') or record_assets(run):
                            self.asset_annotations.setdefault(asset['id'],{'rating':stack['rating'],'favorite':stack['favorite']})
                    existing=next((r for r in parent['runs'] if path_key(r['medium'])==path_key(run['medium'])),None)
                    if existing is None:parent['runs'].append(run)
                if stack.get('time',0)>parent.get('time',0):
                    parent.update({k:stack[k] for k in ('path','time','bytes') if k in stack})
                self.results.remove(stack);changed=True;break

    def register_result(self, record):
        """One card per source/frame, with every run and its explicit artifacts."""
        previous=deepcopy(self.results);previous_annotations=deepcopy(self.asset_annotations)
        record=deepcopy(record)
        if record.get('command')=='scan':
            prior=next((r for s in self.results for r in s['runs'] if path_key(r['medium'])==path_key(record['medium'])),None)
            if prior is not None:
                record={**deepcopy(prior),'cover':record['cover'],'artifacts':{**prior.get('artifacts',{}),**record['artifacts']}}
        record['assets']=record_assets(record,inspect=True)
        record['family']=next((a['family'] for a in record['assets'] if path_key(a['path'])==path_key(record['medium'])),record.get('family','film'))
        stack=next((s for s in self.results if s['key']==record['key'] or any(path_key(r['medium'])==path_key(record['medium']) for r in s['runs'])),None)
        if stack is None:
            stack={'id':str(uuid4()),'key':record['key'],'rating':0,'favorite':False,'runs':[]}
            self.results.append(stack)
        run=next((r for r in stack['runs'] if r['medium']==record['medium'] or
            (record.get('result_id') is not None and r.get('result_id')==record['result_id'])),None)
        if run is None:stack['runs'].append(deepcopy(record))
        else:
            old_assets={a['role']:a for a in run.get('assets',[])}
            for asset in record['assets']:
                old=old_assets.get(asset['role'])
                if old and old['id']!=asset['id'] and old['id'] in self.asset_annotations:
                    self.asset_annotations.setdefault(asset['id'],deepcopy(self.asset_annotations[old['id']]))
            artifacts={**run.get('artifacts',{}),**record.get('artifacts',{})}
            run.update(deepcopy(record));run['artifacts']=deepcopy(artifacts)
            run['assets']=record_assets(run,inspect=True)
        cover=record.get('cover') or next((v for k,v in record['artifacts'].items() if 'visual_preview' in k),'')
        stack.update(label=record.get('label',Path(record['source']).name),path=cover,
                     time=time(),bytes=Path(cover).stat().st_size if cover and Path(cover).exists() else 0)
        self._merge_lineage()
        try:self.save()
        except Exception:self.results=previous;self.asset_annotations=previous_annotations;raise

    def import_paths(self, paths):
        """Per-item failures do not discard successful imports or modify source files."""
        failures=[]
        for path in paths:
            try:
                path=Path(path).resolve()
                if not path.exists():raise ValueError('File moved or missing')
                self.hidden_paths.discard(path_key(path))
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    # Recognize only explicitly registered output members, not arbitrary neighboring images.
                    try:record=import_record(path)
                    except ValueError:record=None
                    if record is not None:self.register_result(record)
                    elif path.name.lower().endswith(('.scanner_raw.tiff','.light_table_raw.tiff')):
                        self.register_result({'key':'archive:'+path_key(path),'source':'','label':path.name,
                                              'medium':str(path),'family':'film','artifacts':{}})
                    else:self.add([path])
                else:self.register_result(import_record(path))
            except (OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:failures.append({'path':str(path),'error':str(exc)})
        self.save()
        return failures

    def add(self, paths):
        seen = {os.path.normcase(str(Path(e['path']).resolve())) for e in self.entries}
        added = []
        for source in paths:
            path = Path(source).resolve()
            key = os.path.normcase(str(path))
            if key in seen or path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                continue
            stat = path.stat()
            added.append({'id':str(uuid4()), 'path':str(path), 'rating':0, 'favorite':False,
                          'time':stat.st_mtime, 'bytes':stat.st_size})
            seen.add(key)
        if added:
            previous = self.entries
            self.entries = [*previous, *added]
            try: self.save()
            except Exception:
                self.entries = previous
                raise
        return added

    def annotate(self, identity, *, rating=None, favorite=None):
        if rating is not None and (type(rating) is not int or not 0 <= rating <= 5):
            raise ValueError('Rating must be 0..5')
        if favorite is not None and type(favorite) is not bool:
            raise ValueError('Favorite must be boolean')
        if identity.startswith('asset:'):
            source=next((e for e in self.entries if 'asset:'+path_key(e['path'])==identity),None)
            if source is not None:return self.annotate(source['id'],rating=rating,favorite=favorite)
            current=next(e for e in self.assets() if e['id']==identity)
            previous=deepcopy(self.asset_annotations)
            annotation=self.asset_annotations.setdefault(identity,{'rating':current['rating'],'favorite':current['favorite']})
            if rating is not None:annotation['rating']=rating
            if favorite is not None:annotation['favorite']=favorite
            try:self.save()
            except Exception:self.asset_annotations=previous;raise
            return
        item = next(e for e in self.entries+self.results if e['id'] == identity)
        previous = dict(item)
        if rating is not None: item['rating'] = rating
        if favorite is not None: item['favorite'] = favorite
        try: self.save()
        except Exception:
            item.clear(); item.update(previous)
            raise

    def assets(self, stack=None, *, _entries=None):
        entries={path_key(e['path']):e for e in self.entries} if _entries is None else _entries
        items={}
        for group in ([stack] if stack is not None else self.results):
            origin=next((r.get('source') for r in group['runs'] if r.get('source')), '')
            source_terms=' '.join([group.get('label',''),origin,*[r.get('source','') for r in group['runs']]])
            for run in group['runs']:
                for stored in run.get('assets') or record_assets(run):
                    asset=deepcopy(stored);identity=asset['id']
                    source_entry=entries.get(path_key(asset['path']))
                    annotation=source_entry or self.asset_annotations.get(identity) or group
                    asset.update(rating=annotation.get('rating',0),favorite=annotation.get('favorite',False),stack_id=group['id'],
                                 source=run.get('source',''),origin=origin,source_terms=source_terms,
                                 label=Path(asset['path']).name,run_medium=run['medium'])
                    # A saved material wins over a later run's generic source reference.
                    if identity not in items or items[identity]['role']=='source':items[identity]=asset
        if stack is None:
            for entry in self.entries:
                identity='asset:'+path_key(entry['path'])
                if identity in items:continue
                asset=asset_descriptor(entry['path'],'source')
                asset.update(rating=entry['rating'],favorite=entry['favorite'],label=Path(entry['path']).name,
                             source=entry['path'],origin=entry['path'],source_terms=entry['path'])
                items[identity]=asset
        return list(items.values())

    def query(self, *, text='', favorites=False, relation='any', rating=0, sort='time', results=False,
              collection=None, kind='', family='', source=''):
        def accepted(item):
            stars=item['rating']
            compare={'any':True,'eq':stars==rating,'gt':stars>rating,'lt':stars<rating,'ge':stars>=rating,'le':stars<=rating}
            return (path_key(item['path']) not in self.hidden_paths and (not favorites or item['favorite']) and compare[relation]
                    and text.casefold() in (item.get('label',Path(item['path']).name)+' '+item.get('role','')).casefold()
                    and (not kind or kind in item.get('types',('source',)))
                    and (not family or family==item.get('family',''))
                    and source.casefold() in item.get('source_terms',item['path']).casefold())
        mode=collection or ('results' if results else 'sources')
        if mode=='assets':result=[a for a in self.assets() if accepted(a)]
        elif mode=='results':
            result=[]
            source_entries={path_key(e['path']):e for e in self.entries}
            for stack in self.results:
                matches=[a for a in self.assets(stack,_entries=source_entries) if accepted(a)]
                if matches:result.append(dict(stack,matched_assets=[a['id'] for a in matches]))
        else:result=[item for item in self.entries if accepted(item)]
        result.sort(key=lambda e:e['id'])
        result.sort(key=lambda e:Path(e['path']).name.casefold() if sort=='name' else e[sort], reverse=sort!='name')
        return result
