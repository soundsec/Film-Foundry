"""Data-only desktop translations. Missing entries fall back to built-in text."""
from functools import lru_cache
import json
from pathlib import Path
import re
from string import Formatter

from film_foundry.core.atomic_io import atomic_output_path, strict_json_loads

MAX_SIZE=8*1024*1024


def shared_directory():
    from film_foundry.app.paths import app_root
    return app_root() / 'languages'


def pack_directory(settings_path=None):
    from film_foundry.ui.preferences import preferences_path
    path=settings_path or preferences_path()
    return path.with_name(path.stem+'_languages')


def fields(value):
    return sorted((name, spec, conversion or '') for _,name,spec,conversion in Formatter().parse(value) if name is not None)


def validate(value):
    from film_foundry.ui.i18n import _EN, _ZH
    if not isinstance(value,dict) or value.get('format')!='film-foundry-language' or type(value.get('version')) is not int or value['version']!=1:
        raise ValueError('Expected Film Foundry language pack version 1')
    code=value.get('code','')
    if not isinstance(code,str) or not re.fullmatch(r'[a-z]{2,3}(?:_[A-Za-z0-9]{2,8}){0,2}',code) or code in ('zh_CN','en_US','zh','en'):
        raise ValueError('Use a new language code, e.g. fr_FR; built-in languages cannot be replaced')
    name=value.get('name');fallback=value.get('fallback','en_US');entries=value.get('translations')
    if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('Language name is required (1–80 characters)')
    if fallback not in ('en_US','zh_CN'):raise ValueError('Fallback must be en_US or zh_CN')
    if not isinstance(entries,dict) or len(entries)>20000:raise ValueError('Invalid translations dictionary')
    for key,translation in entries.items():
        if not isinstance(key,str) or not key.startswith(('key:','text:')) or not isinstance(translation,str) or len(key)>20000 or len(translation)>20000:
            raise ValueError('Translations must map key: / text: identifiers to strings')
        source=key[5:] if key.startswith('text:') else (_EN.get(key[4:]) or _ZH.get(key[4:]))
        if translation and source is not None:
            try:valid=fields(source)==fields(translation)
            except ValueError:valid=False
            if not valid:raise ValueError('Keep all format placeholders unchanged: '+key[:120])
    return dict(format='film-foundry-language',version=1,code=code,name=name.strip(),fallback=fallback,translations=entries)


def read_pack(path):
    path=Path(path)
    if path.stat().st_size>MAX_SIZE:raise ValueError('Language pack exceeds 8 MiB')
    return validate(strict_json_loads(path.read_text(encoding='utf-8-sig')))


@lru_cache(maxsize=4)
def _installed(directory):
    packs={}
    for location in (shared_directory(), directory):
        for path in sorted(location.glob('*.json')):
            try:
                pack=read_pack(path)
                if path.stem==pack['code']:packs[pack['code']]=pack
            except (OSError,ValueError,TypeError,RecursionError):continue
    return packs


def installed(settings_path=None):return _installed(pack_directory(settings_path))


def refresh_packs():
    from film_foundry.ui import i18n
    _installed.cache_clear()
    packs = installed()
    if i18n.current_language() not in ('zh_CN', 'en_US', *packs):
        i18n.set_language(i18n.DEFAULT_LANGUAGE)
    return packs


def import_pack(path):
    pack=read_pack(path);destination=pack_directory()/(pack['code']+'.json')
    with atomic_output_path(destination) as temporary:
        temporary.write_text(json.dumps(pack,ensure_ascii=False,indent=2),encoding='utf-8')
    _installed.cache_clear()
    return pack['code']


def export_template(path, language=None):
    from film_foundry.ui.i18n import _EN,_ZH
    from film_foundry.ui.translation_catalog import TEXTS
    reference={'key:'+key:dict(en=_EN.get(key,''),zh=_ZH.get(key,'')) for key in sorted(_ZH.keys()|_EN.keys())}
    reference.update({'text:'+en:dict(en=en,zh=zh) for en,zh in TEXTS.items()})
    existing=installed().get(language,{})
    value=dict(format='film-foundry-language',version=1,code=existing.get('code','xx_XX'),
        name=existing.get('name','My language'),fallback=existing.get('fallback','en_US'),
        translations={key:existing.get('translations',{}).get(key,'') for key in reference},reference=reference)
    # Keep newer / manually added entries when exporting an installed pack for revision.
    value['translations'].update(existing.get('translations',{}))
    with atomic_output_path(Path(path)) as temporary:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def translate_pair(zh,en):
    from film_foundry.ui.i18n import current_language
    code=current_language()
    if code in ('zh_CN','en_US'):return zh if code=='zh_CN' else en
    pack=installed().get(code)
    if pack:
        return pack['translations'].get('text:'+en) or (zh if pack['fallback']=='zh_CN' else en)
    return zh if code.startswith('zh') else en
