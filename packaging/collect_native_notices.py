"""Record actual PyInstaller native origins and retain matching Conda notices.
No hashes; private source paths stay in the local build TOC, never the inventory.
"""
from pathlib import Path
import argparse,ast,json,shutil,os

def main():
 p=argparse.ArgumentParser();p.add_argument('--toc',type=Path,required=True);p.add_argument('--python-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 records=[]
 def walk(value):
  if isinstance(value,(list,tuple)):
   if len(value)==3 and all(isinstance(x,str) for x in value) and value[2] in ('BINARY','EXTENSION'): records.append(value)
   else:
    for child in value:walk(child)
 walk(ast.literal_eval(a.toc.read_text(encoding='utf8')))
 origins={str((a.python_root/f).resolve()).lower():r for path in (a.python_root/'conda-meta').glob('*.json') for r in [json.loads(path.read_text())] for f in r.get('files',[])}
 a.output.mkdir(parents=True,exist_ok=True)
 packages={};files=[]
 for target,source,kind in sorted(set(tuple(x) for x in records)):
  if Path(target).name.lower()=='icuuc.dll':continue # deliberately excluded by the spec
  source_path=Path(source).resolve();key=str(source_path).lower();record=origins.get(key)
  if record:
   owner=f"{record['name']} {record['version']} {record['build']}"
   if owner not in packages:
    cache=Path(record['link']['source']); license_root=cache/'info/licenses'
    folder=a.output/'conda'/f"{record['name']}-{record['version']}"
    if not license_root.is_dir():raise RuntimeError('Missing original notices: '+owner)
    folder.mkdir(parents=True,exist_ok=True)
    notices=[]
    for notice in license_root.rglob('*'):
     if notice.is_file():
      dest=folder/(notice.relative_to(license_root).as_posix().replace('/','__')+'.txt')
      shutil.copyfile(notice,dest);notices.append(dest.relative_to(a.output).as_posix())
    about=json.loads((cache/'info/about.json').read_text()) if (cache/'info/about.json').exists() else {}
    packages[owner]={'name':record['name'],'version':record['version'],'build':record['build'],'declared_license':record.get('license'),'home':about.get('home'),'development_url':about.get('dev_url'),'notices':notices}
  elif source_path.is_relative_to(a.python_root.resolve()):
   owner='Python environment / wheel; see runtime/dependencies notices'
  elif source_path.is_relative_to(Path(os.environ['SystemRoot']).resolve()):
   owner='Windows system runtime'
  else:raise RuntimeError('Native dependency outside selected Python or Windows: '+target)
  files.append({'file':target.replace('\\','/'),'owner':owner})
 (a.output/'native-files.json').write_text(json.dumps({'files':files,'conda_packages':list(packages.values())},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 (a.output/'README.md').write_text('# Windows native dependency inventory\n\nGenerated from the actual PyInstaller binary table after restricting PATH to the selected Python environment and Windows. Relative packaged paths only; no hashes or private build paths.\n\n`native-files.json` records '+str(len(files))+' binaries and '+str(len(packages))+' matching Conda package records. `conda/` retains original notices from those exact cached package builds. Wheel notices are in the portable licenses/dependencies directory (or third_party_licenses/runtime/dependencies in source snapshots). The Qt system `icuuc.dll` is deliberately not bundled.\n\nPackage metadata can cover more tools than the DLLs actually included; retain the original notices rather than inferring that every package executable is shipped. Source availability and rebuild instructions must accompany binary publication where required; this inventory is not a source archive.\n',encoding='utf8')
 print(f'Recorded {len(files)} native files and {len(packages)} Conda notice sets')
if __name__=='__main__':main()
