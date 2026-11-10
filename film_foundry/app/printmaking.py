"""Portable TIFF/JSON creative plate projects and fixed observation sources."""
from dataclasses import asdict
from pathlib import Path
import copy
import numpy as np
import tifffile
from film_foundry.core.printmaking import PlateStyle,separate,compose
from film_foundry.core.atomic_io import atomic_output_directory,atomic_write_json,strict_json_load
from film_foundry.app.print_export import export_print


class PrintmakingSession:
    def __init__(self):
        self.image=None;self.plates=[];self.styles=[];self.names=[];self.provenance={};self.components={}
        self.dpi=300.;self.paper=(1.,.98,.94);self.result=None
        self.protected_paths=[]
        self.registration=True

    @property
    def source_name(self):
        return self.provenance.get('name') or (Path(self.provenance['path']).name if self.provenance.get('path') else '')

    def set_image(self,image,provenance=None,components=None):
        a=np.asarray(image,dtype=np.float32)
        if a.ndim!=3 or a.shape[-1]!=3 or not a.size or not np.isfinite(a).all():raise ValueError('Expected finite RGB observation')
        self.image=np.clip(a,0,1).copy();self.provenance=copy.deepcopy(provenance or {});self.components={}
        self.protected_paths=[self.provenance['path']] if self.provenance.get('path') else []
        for key,value in (components or {}).items():
            a=np.asarray(value,dtype=np.float32)
            if a.ndim==2 and a.shape==self.image.shape[:2] and np.isfinite(a).all() and np.min(a)>=0:
                self.components[key]=a if not a.flags.writeable else a.copy()
        self.separate('cmy')

    def separate(self,mode):
        if self.image is None:raise ValueError('Choose an image first')
        self.plates,inks,self.names=separate(self.image,mode)
        self.styles=[PlateStyle(ink=ink,angle=angle) for ink,angle in zip(inks,(15,75,0))];self.result=None

    def add_plate(self,coverage,name,*,source_path=None):
        if self.image is None or np.asarray(coverage).shape!=self.image.shape[:2]:raise ValueError('Imported plate must match the image dimensions')
        if len(self.plates)>=32:raise ValueError('At most 32 plates')
        a=np.asarray(coverage,dtype=np.float32)
        if not np.isfinite(a).all():raise ValueError('Invalid coverage map')
        self.plates.append(np.clip(a,0,1).copy());self.names.append(str(name));self.styles.append(PlateStyle());self.result=None
        if source_path:self.protected_paths.append(str(Path(source_path).resolve()))

    def render(self):
        self.result,masks=compose(self.plates,self.styles,dpi=self.dpi,paper=self.paper)
        return self.result,masks

    def save(self,directory,*,registration=True):
        directory=Path(directory).resolve()
        if directory.exists():raise ValueError('Choose a new project directory')
        if any(Path(source).resolve().is_relative_to(directory) for source in self.protected_paths):raise ValueError('Project must not replace its source directory')
        result,masks=self.render()
        with atomic_output_directory(directory,replace_existing=False) as staging:
            export_print(self.image,staging/'source.tif',dpi=self.dpi,metadata=self.provenance)
            export_print(result,staging/'composite.tif',dpi=self.dpi,metadata={'process':'creative_transparent_ink','source':self.provenance})
            rows=[]
            for i,(plate,mask,style,name) in enumerate(zip(self.plates,masks,self.styles,self.names)):
                coverage=f'coverage_{i+1:02d}.tif';screen=f'plate_{i+1:02d}.tif'
                tifffile.imwrite(staging/coverage,np.uint16(np.clip(plate,0,1)*65535+.5),photometric='minisblack',metadata=None,compression='deflate')
                image=1-mask
                margin=max(8,round(8*self.dpi/25.4)) if registration else 0
                if margin:
                    image=np.pad(image,margin,constant_values=1)
                    arm=max(2,margin//4);stroke=max(1,round(self.dpi/300))
                    for y in (margin//2,image.shape[0]-margin//2):
                        for x in (margin//2,image.shape[1]-margin//2):
                            image[y-stroke:y+stroke+1,x-arm:x+arm+1]=0
                            image[y-arm:y+arm+1,x-stroke:x+stroke+1]=0
                tifffile.imwrite(staging/screen,np.uint16(np.clip(image,0,1)*65535+.5),photometric='minisblack',resolution=(self.dpi,self.dpi),resolutionunit='INCH',metadata=None,compression='deflate')
                rows.append({'name':name,'coverage':coverage,'output':screen,'style':asdict(style)})
            atomic_write_json(staging/'project.json',{'model':'creative_plates_v1','source':'source.tif','provenance':self.provenance,
                'dpi':self.dpi,'paper':self.paper,'plates':rows,'registration_margin_mm':8 if registration else 0,
                'coverage_polarity':'white_is_coverage','output_polarity':'black_is_ink','separation':'creative_not_press_cmyk'})
        return directory/'project.json'

    def load(self,path):
        path=Path(path);data=strict_json_load(path)
        if data.get('model')!='creative_plates_v1' or not 1<=len(data['plates'])<=32:raise ValueError('Invalid plate project')
        def read(name):
            if Path(name).name!=name:raise ValueError('Project members must be local filenames')
            a=tifffile.imread(path.parent/name)
            if a.dtype!=np.uint16:raise ValueError('Project masters must be 16-bit TIFF')
            return a.astype(np.float32)/65535
        candidate=PrintmakingSession();candidate.set_image(read(data['source']),data.get('provenance'))
        candidate.plates=[];candidate.styles=[];candidate.names=[]
        for row in data['plates']:
            candidate.add_plate(read(row['coverage']),row['name']);candidate.styles[-1]=PlateStyle(**row['style'])
        candidate.dpi=float(data['dpi']);candidate.paper=tuple(data['paper']);candidate.registration=bool(data.get('registration_margin_mm'));candidate.render()
        candidate.protected_paths.extend(str(path.parent / name) for name in [data['source'],path.name,*[r['coverage'] for r in data['plates']],*[r['output'] for r in data['plates']]])
        self.__dict__.update(candidate.__dict__)
