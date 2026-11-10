"""Family-specific structures in declared millimetres, frozen before observation."""
import math
import numpy as np
from film_foundry.core.material_structure import Grid


def _fibers(g,stream):
    scale=g.material.texture_scale_mm;rng=g.rng(stream+50)
    # Several oblique populations avoid a screen-aligned woven-cloth grid.
    return np.clip(.45*g.noise(stream,scale,5.,rng.uniform(12,45))+
                   .35*g.noise(stream+7,scale*1.6,4.,rng.uniform(60,120))+
                   .20*g.noise(stream+14,scale*.7,1.3,rng.uniform(-50,-10)),0,1)


def paper_structure(shape,seed,m):
    g=Grid(shape,seed,m);cloud=g.noise(4101,m.process_scale_mm)
    fiber=_fibers(g,4111)
    cover=(1-m.coating_variation*cloud)*(1-m.edge_loss*np.exp(-g.edge_distance()/(m.process_scale_mm*.35)))
    paper=1-m.texture*.12*fiber
    return g.fit(cover),g.fit(paper)


def wet_structure(shape,seed,m):
    g=Grid(shape,seed,m);rng=g.rng(1601);along,across,length,breadth=g.axes(m.pour_direction)
    scale=m.process_scale_mm;flow=np.zeros(g.shape,np.float32)
    for _ in range(min(64,max(1,round(length/(2.3*scale))))):
        center=rng.uniform(.08,.92)*length+rng.uniform(-.6,.6)*scale*((across/breadth-.5)**2*4)
        center+=rng.uniform(-.25,.25)*scale*(across/breadth-.5)
        width=max(g.pitch,rng.uniform(.25,.9)*scale)
        flow+=np.exp(-((along-center)/width)**2)*rng.uniform(.15,.35)
    flow=np.clip(flow,0,1)
    thin=(1-m.coating_variation*g.noise(1602,scale))*(1-m.flow_ridges*.8*flow)
    pooled=m.edge_pooling*np.exp(-(length-along)/(scale*.55))*(1-thin)
    cover=(thin+pooled)*(1-m.edge_loss*np.exp(-g.edge_distance()/(scale*.22)))
    holes=np.zeros(g.shape,np.float32)
    if m.pinholes:
        for _ in range(min(256,max(1,round(g.height_mm*g.width_mm/(scale*scale*3.))))):
            cx=rng.uniform(.03,.97)*g.width_mm;cy=rng.uniform(.03,.97)*g.height_mm
            radius=m.texture_scale_mm*rng.uniform(.15,.6)
            width2=radius*radius+g.pitch*g.pitch/12
            holes=np.maximum(holes,radius*radius/width2*np.exp(-((g.x-cx)**2+(g.y-cy)**2)/(2*width2)))
    cover*=1-m.pinholes*holes
    rough=m.texture*(.15+.85*g.noise(1610,m.texture_scale_mm))
    return g.fields(coating=cover,flow=flow,roughness=rough,surface=1-.12*rough)


def cyan_structure(shape,seed,m):
    g=Grid(shape,seed,m);rng=g.rng(2601);along,across,length,breadth=g.axes(m.brush_direction)
    # Unresolved passes merge into an average coating; the bounded loop must
    # still span the whole sheet, even at the largest extent/smallest scale.
    requested=m.process_scale_mm;scale=max(requested,breadth/72,2*g.pitch)
    brush=np.zeros(g.shape,np.float32);center=-scale*.5
    for _ in range(128):
        center+=rng.uniform(.65,1.3)*scale
        if center>breadth+scale:break
        bend=center+rng.uniform(-.4,.4)*scale*(along/length-.5)+rng.uniform(-.7,.7)*scale*(along/length-.5)**2
        width=max(g.pitch,rng.uniform(.45,.95)*scale*(1+.25*m.brush_overlap))
        start=rng.uniform(0,.35)*scale;end=length-rng.uniform(0,.35)*scale
        reach=np.clip((along-start)/max(g.pitch,scale*.12),0,1)*np.clip((end-along)/max(g.pitch,scale*.12),0,1)
        brush+=np.exp(-((across-bend)/width)**4)*reach*rng.uniform(.45,.75)
    brush=.7+min(1.,requested/scale)*(np.clip(brush,0,1)-.7)
    fibers=g.noise(2610,m.texture_scale_mm)
    cover=(1-m.coating_variation*(1-brush))*(1-m.edge_loss*np.exp(-g.edge_distance()/(requested*.15))*(1-.6*brush))
    cover*=1-m.texture*.12*fibers
    return g.fields(coating=cover,paper=1-m.texture*.16*fibers,binding=np.clip(m.pigment_binding*(.7+.3*fibers),0,1),fibers=fibers)


def platinum_structure(shape,seed,m):
    g=Grid(shape,seed,m);fiber=_fibers(g,3601)
    absorb=(1-m.paper_sizing)*(.45+.55*fiber)+m.paper_sizing*.35
    mottling=g.noise(3620,m.process_scale_mm)
    if m.coating_method=='brush':mottling=.45*mottling+.55*g.noise(3630,m.process_scale_mm,.2)
    cover=(1-m.coating_variation*(.15+.5*mottling+.35*absorb))*(1-m.edge_loss*np.exp(-g.edge_distance()/(m.process_scale_mm*.1)))
    cover*=1-m.texture*.12*fiber
    return g.fields(coating=cover,paper=1-m.texture*.13*fiber,absorption=absorb,clear_access=np.clip(1-.55*absorb,0,1),fibers=fiber)


def polish(shape,seed,m,angle_degrees):
    g=Grid(shape,seed,m);a=math.radians(angle_degrees);rng=g.rng(501)
    across=g.x*math.cos(a)+g.y*math.sin(a);along=-g.x*math.sin(a)+g.y*math.cos(a)
    lines=np.zeros(g.shape,np.float32)
    for _ in range(9):
        frequency=rng.uniform(.4,2.2)/m.texture_scale_mm
        attenuation=math.exp(-(frequency*4*g.pitch)**4)
        lines+=attenuation*np.sin(2*np.pi*(across*frequency+.12*np.sin(along*rng.uniform(.3,.9)/m.process_scale_mm))+rng.uniform(0,2*np.pi))/9
    return g.fit(lines*(.4+.6*g.noise(502,m.process_scale_mm))),g.fit(g.noise(512,m.texture_scale_mm))


def transfer_flow(shape,seed,m,direction,leakage,unevenness):
    g=Grid(shape,seed,m);rng=g.rng(411);along,across,length,breadth=g.axes(direction)
    scale=m.process_scale_mm;field=g.noise(420,scale);lanes=np.zeros(g.shape,np.float32);roller=np.zeros(g.shape,np.float32)
    pass_scale=max(scale,breadth/72,2*g.pitch);center=-pass_scale
    for _ in range(128):
        center+=rng.uniform(.65,1.6)*pass_scale
        if center>breadth+pass_scale:break
        bend=.2*pass_scale*np.sin(along/length*rng.uniform(3,7)+rng.uniform(0,6))
        lane=np.exp(-((across-center-bend)/max(g.pitch,rng.uniform(.4,.9)*pass_scale))**2)
        lanes+=lane*rng.uniform(.55,1.)
        roller=np.maximum(roller,lane*rng.uniform(.25,.85))
    contrast=min(1.,scale/pass_scale)
    lanes=.7+contrast*(lanes-.7);roller=.4+contrast*(roller-.4)
    roller*=.55+.45*g.noise(430,scale,2.)
    # Coarser-than-sheet channels must not push the entire initial budget
    # outside the material. Front setback saturates within its travel extent.
    travel_scale=min(scale,length*.15)
    front=length-unevenness*travel_scale*(1+3*(1-np.clip(lanes,0,1))+1.2*field)
    reached=np.clip((front-along)/max(g.pitch,scale*.25)+.5,0,1) if unevenness else np.ones(g.shape,np.float32)
    quantity=(1+unevenness*(lanes-.7))*reached
    side=across if rng.random()<.5 else breadth-across
    width=scale*(.25+1.5*leakage*(.35+.65*field))
    escape=leakage*np.exp(-side/width)*(.3+.7*along/length)
    remaining=g.fit(quantity*(1-np.clip(escape,0,1)));escaped=g.fit(quantity*np.clip(escape,0,1))
    # Normalize after sampling to preserve the original per-pixel budget.
    total=max(float((remaining+escaped).mean()),1e-6)
    remaining=remaining/total;escaped=escaped/total
    return remaining,escaped,escaped*(.4+1.5*g.fit(field)),g.fit(roller)


def wear(shape,seed,m,scratches,pitting,edge_tarnish,*,separate=False):
    g=Grid(shape,seed,m);rng=g.rng(303);loss=np.zeros(g.shape,np.float32)
    if scratches:
        for _ in range(18):
            cx=rng.uniform(0,g.width_mm);cy=rng.uniform(0,g.height_mm);a=rng.uniform(-.4,.4)
            across=(g.x-cx)*math.cos(a)-(g.y-cy)*math.sin(a);along=(g.x-cx)*math.sin(a)+(g.y-cy)*math.cos(a)
            width=m.texture_scale_mm*rng.uniform(.08,.3);filtered=math.sqrt(width*width+g.pitch*g.pitch/12)
            line=width/filtered*np.exp(-.5*(across/filtered)**2)*np.exp(-(along/(m.process_scale_mm*rng.uniform(.6,3.)))**4)
            loss=np.maximum(loss,line*scratches*rng.uniform(.4,1))
    pits=np.clip((g.noise(304,m.texture_scale_mm)-.5)*3,0,1)*pitting
    abrasion=loss
    loss=1-(1-loss)*(1-pits)
    coarse=g.noise(314,m.process_scale_mm);fine=g.noise(324,m.texture_scale_mm)
    tarnish=edge_tarnish*np.exp(-g.edge_distance()/(m.process_scale_mm*(.2+2.4*coarse**2)))*(.45+.55*fine)
    result=g.fit(loss),g.fit(pits),g.fit(tarnish)
    return (*result,g.fit(abrasion)) if separate else result
