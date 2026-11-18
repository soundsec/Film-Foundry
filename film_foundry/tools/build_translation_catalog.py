"""Refresh bundled translation references without importing GUI windows."""
import ast
from pathlib import Path
import pprint


def collect():
    root=Path(__file__).resolve().parents[1];pairs={}
    def add(zh,en):
        if isinstance(zh,str) and isinstance(en,str) and any('\u3400'<=c<='\u9fff' for c in zh) and not any('\u3400'<=c<='\u9fff' for c in en):
            pairs.setdefault(en,zh)
    for directory in ('ui','tools'):
        for path in sorted((root/directory).glob('*.py')):
            if path.name in ('translation_catalog.py','build_translation_catalog.py'):continue
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                values=node.args if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('text','local_text','translate_pair') else node.elts if isinstance(node,(ast.Tuple,ast.List)) else []
                for left,right in zip(values,values[1:]):
                    if isinstance(left,ast.Constant) and isinstance(right,ast.Constant):add(left.value,right.value)
                if isinstance(node,ast.Dict):
                    for key,value in zip(node.keys,node.values):
                        if isinstance(key,ast.Constant) and isinstance(value,ast.Constant):add(key.value,value.value)
    destination=root/'ui/translation_catalog.py'
    destination.write_text('"""Generated static translation references; refresh with tools.build_translation_catalog."""\nTEXTS = '+pprint.pformat(dict(sorted(pairs.items())),width=120,sort_dicts=False)+'\n',encoding='utf-8')
    return len(pairs)


if __name__=='__main__':print(f'{collect()} translation references refreshed')
