"""Aperture measurements of finished material bodies under a unit illuminant."""
from dataclasses import replace
import math
import numpy as np
from film_foundry.app.material_families import observe
from film_foundry.core.material_family import observation_border
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtWidgets


def sample_density(body, view, x, y, aperture):
    height,width=next(iter(body.fields.values())).shape[:2]
    if not (0<=x<width and 0<=y<height):return None
    r=aperture//2
    rows=slice(max(0,y-r),min(height,y+r+1))
    cols=slice(max(0,x-r),min(width,x+r+1))
    patch=replace(body,fields={key:array[rows,cols] for key,array in body.fields.items()})
    unit=replace(view,include_border=False,illuminant_rgb=(1.,1.,1.),exposure_ev=0.)
    radiance=observe(patch,unit)
    return -np.log10(np.maximum(radiance,1e-10)).mean(axis=(0,1))


class MaterialProbe(QtCore.QObject):
    def __init__(self,owner,menu,layout):
        super().__init__(owner.viewer)
        self.owner=owner;self.cache_key=None;self.aperture=0
        settings=menu.addMenu(text('材料密度探针','Material density probe'))
        self.pins=[];self.source=None;self.actions=[]
        self.readout=QtWidgets.QLabel();self.readout.setWordWrap(True);self.readout.hide()
        layout.addWidget(self.readout)
        for size in (0,1,5,15):
            action=settings.addAction(text('关闭','Off') if not size else f'{size} × {size}')
            action.setCheckable(True);action.setChecked(size==0)
            action.triggered.connect(lambda checked=False,n=size:self.set_aperture(n))
            self.actions.append((size,action))
        settings.addSeparator()
        settings.addAction(text('清除钉点','Clear pins'),self.clear)
        owner.viewer.probe_overlay=self.draw_pins
        owner.viewer.viewport().installEventFilter(self)

    def clear(self):
        self.pins.clear();self.readout.hide()
        self.owner.viewer.viewport().update()

    def draw_pins(self,painter):
        viewer=self.owner.viewer
        group=self.owner.session.group
        if (not self.aperture or not self.pins or group is None or viewer.result is None
                or viewer.result.cacheKey()!=self.cache_key or self.index>=len(group.bodies)):
            return
        body=group.bodies[self.index]
        if body.body_id!=self.body_id:return
        h,w=next(iter(body.fields.values())).shape[:2]
        top,bottom,left,right=(0,0,0,0) if self.owner.kind=='plate' else observation_border(body,(h,w),self.conditions)
        width,height=w+left+right,h+top+bottom
        points=[]
        for i,(u,v) in enumerate(self.pins,1):
            x,y=int(u*w)+left,int(v*h)+top
            q=self.turns%4
            if q==1:x,y=height-1-y,x
            elif q==2:x,y=width-1-x,height-1-y
            elif q==3:x,y=y,width-1-x
            points.append((i,x,y))
        viewer.draw_probe_points(painter,points)

    def show_pins(self):
        self.owner.viewer.viewport().update()
        group=self.owner.session.group
        if not self.aperture or not self.pins or self.cache_key is None or group is None:
            self.readout.hide();return
        body=group.bodies[self.index]
        h,w=next(iter(body.fields.values())).shape[:2]
        lines=[]
        for i,(u,v) in enumerate(self.pins,1):
            x,y=int(u*w),int(v*h)
            values=sample_density(body,self.conditions,x,y,self.aperture)
            lines.append(f'{i}  ({x}, {y})  '+', '.join(f'{value:.3f}' for value in values))
        label=text('透射密度','Transmission density') if body.usable_negative else text('反射密度','Reflection density')
        self.readout.setText(label+' (RGB)   '+'    '.join(lines));self.readout.show()

    def set_aperture(self,size):
        self.aperture=size
        self.owner.viewer.viewport().setMouseTracking(bool(size))
        for n,action in self.actions:action.setChecked(n==size)
        self.show_pins()

    def publish(self,index,view):
        self.cache_key=None
        owner=self.owner
        group=owner.session.group
        inspection=getattr(owner,'inspection',None)
        if group is None or (inspection is not None and inspection.currentData()!='image'):
            self.readout.hide();owner.viewer.viewport().update();return
        self.index=min(index,len(group.bodies)-1)
        self.body_id=group.bodies[self.index].body_id
        self.conditions=view;self.turns=owner.session.scan_adjustments.get('rotation_quarters',0)
        self.cache_key=owner.viewer.result.cacheKey()
        source=(owner.session.source_path,id(owner.session.image),group.bodies[self.index].role)
        if self.source!=source:self.clear();self.source=source
        self.show_pins()

    def eventFilter(self,target,event):
        pin=(event.type()==QtCore.QEvent.Type.MouseButtonPress and event.button()==QtCore.Qt.MouseButton.LeftButton
             and bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
        if (not pin and event.type()!=QtCore.QEvent.Type.MouseMove) or not self.aperture:return False
        owner=self.owner;viewer=owner.viewer;group=owner.session.group
        if (owner.busy or group is None or viewer.result is None or self.cache_key!=viewer.result.cacheKey()
                or viewer._holding or viewer._keyboard_source or viewer.held_preview is not None):return False
        body=group.bodies[self.index]
        if body.body_id!=self.body_id:return False
        h,w=next(iter(body.fields.values())).shape[:2]
        top,bottom,left,right=(0,0,0,0) if owner.kind=='plate' else observation_border(body,(h,w),self.conditions)
        pos=viewer.mapToScene(event.position().toPoint())
        x,y=math.floor(pos.x()),math.floor(pos.y())
        displayed=viewer.displayed_result()
        if viewer.mode=='side_by_side':x-=displayed.width()
        if viewer.mode=='wipe' and x<displayed.width()*viewer.divider:return False
        if not (0<=x<displayed.width() and 0<=y<displayed.height()):return False
        width=w+left+right;height=h+top+bottom
        turns=(self.turns+viewer.rotation_quarters)%4
        if turns==1:x,y=y,height-1-x
        elif turns==2:x,y=width-1-x,height-1-y
        elif turns==3:x,y=width-1-y,x
        x-=left;y-=top
        values=sample_density(body,self.conditions,x,y,self.aperture)
        if values is not None:
            label=text('透射密度','Transmission density') if body.usable_negative else text('反射密度','Reflection density')
            msg=f'{label} (RGB)  '+', '.join(f'{v:.3f}' for v in values)+f'   ({x}, {y})'
            QtWidgets.QToolTip.showText(event.globalPosition().toPoint(),msg,target)
            if pin:
                self.pins=(self.pins+[((x+.5)/w,(y+.5)/h)])[-4:]
                self.show_pins();return True
        return False
