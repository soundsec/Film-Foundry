"""Deterministic spatial fields for the independent creative material models.

Normalized sheet coordinates, finite footprints and local random generators;
these are material/process inputs, never overlays on an observed RGB image.
"""
import math
import numpy as np


def fraction(value, name):
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and within [0,1]")
    return float(value)


def coordinates(shape):
    h, w = shape
    return ((np.arange(h, dtype=np.float32) + .5) / h)[:, None], ((np.arange(w, dtype=np.float32) + .5) / w)[None, :]


def patches(shape, seed, stream=0, count=14, radius=(.035, .18)):
    """Bounded, irregular smooth patches independent of image content."""
    y, x = coordinates(shape)
    rng = np.random.default_rng(np.random.SeedSequence([seed, stream]))
    field = np.zeros(shape, np.float32)
    for _ in range(count):
        cx, cy = rng.uniform(0, 1, 2)
        rx, ry = rng.uniform(*radius, 2)
        blob = np.exp(-.5 * (((x-cx)/rx)**2 + ((y-cy)/ry)**2))
        field = np.maximum(field, blob * rng.uniform(.55, 1))
    return field


def clouds(shape, seed, stream=0, cells=5):
    """Multiscale continuous nonperiodic variation, avoiding repeated round blobs."""
    y,x=coordinates(shape)
    rng=np.random.default_rng(np.random.SeedSequence([seed,stream]))
    result=np.zeros(shape,np.float32)
    for octave,weight in enumerate((.55,.3,.15)):
        n=min(cells*2**octave,max(1,min(shape)//2))
        grid=rng.random((n+1,n+1),dtype=np.float32)
        gx=x*n;gy=y*n;ix=gx.astype(int);iy=gy.astype(int)
        fx=gx-ix;fy=gy-iy
        fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy)
        result+=weight*((grid[iy,ix]*(1-fx)+grid[iy,ix+1]*fx)*(1-fy)+
                        (grid[iy+1,ix]*(1-fx)+grid[iy+1,ix+1]*fx)*fy)
    return result


def edge_field(shape, seed, edge="left"):
    if edge not in ("left", "right", "top", "bottom"):
        raise ValueError("Unknown exposed edge")
    y, x = coordinates(shape)
    along = x if edge == "left" else 1-x if edge == "right" else y if edge == "top" else 1-y
    irregular = patches(shape, seed, 101, count=9, radius=(.08, .3))
    return np.exp(-along / (.035 + .18 * irregular)).astype(np.float32)


def scratches(shape, seed):
    y, x = coordinates(shape)
    rng = np.random.default_rng(np.random.SeedSequence([seed, 303]))
    field = np.zeros(shape, np.float32)
    for _ in range(18):
        cx, cy = rng.uniform(0, 1, 2)
        angle = rng.uniform(-.4, .4)
        across = (x-cx)*math.cos(angle) - (y-cy)*math.sin(angle)
        along = (x-cx)*math.sin(angle) + (y-cy)*math.cos(angle)
        width = max(.7 / min(shape), rng.uniform(.0008, .003))
        line = np.exp(-.5*(across/width)**2) * np.exp(-(along/rng.uniform(.06, .3))**4)
        field = np.maximum(field, line * rng.uniform(.4, 1))
    return field


def record_event(snapshot, kind, **parameters):
    import json
    settings = json.loads(snapshot)
    events = list(settings.get("events", []))
    events.append(dict(operation=kind, **parameters))
    settings["events"] = events
    return json.dumps(settings, sort_keys=True, allow_nan=False)


def handling_density(shape,seed,*,fingerprints=0.,reagent_residue=0.,position_x=.5,position_y=.5,angle_degrees=0.):
    """A finite fingerprint footprint and dried reagent deposited on the sheet."""
    for name,value in (('fingerprints',fingerprints),('reagent_residue',reagent_residue),('position_x',position_x),('position_y',position_y)):fraction(value,name)
    if not math.isfinite(angle_degrees):raise ValueError('Invalid handling angle')
    y,x=coordinates(shape);angle=math.radians(angle_degrees)
    u=((x-position_x)*math.cos(angle)+(y-position_y)*math.sin(angle))/.095
    v=(-(x-position_x)*math.sin(angle)+(y-position_y)*math.cos(angle))/.15
    radius=np.sqrt(u*u+(v+.15*u*u)**2)
    footprint=np.exp(-2.5*radius**4)
    # Suppress unresolved ridges at small working resolutions (no moire).
    cycles=min(12.,max(.5,min(shape)*.095/5))
    ridges=(.5+.5*np.cos(2*np.pi*cycles*radius))**2
    print_field=fingerprints*footprint*(.025+.14*ridges)
    dried=reagent_residue*patches(shape,seed,781,count=6,radius=(.02,.10))
    return (print_field[...,None]*np.array([.8,.85,1.],np.float32)+dried[...,None]*np.array([.06,.14,.28],np.float32)).astype(np.float32)


def brush_field(shape,angle_degrees=0.):
    if not math.isfinite(angle_degrees):raise ValueError('Invalid brush angle')
    y,x=coordinates(shape);angle=math.radians(angle_degrees)
    across=x*math.cos(angle)+y*math.sin(angle)
    along=-x*math.sin(angle)+y*math.cos(angle)
    cycles=min(45.,max(1,min(shape)/5))
    return ((.5+.5*np.sin(across*2*np.pi*cycles+.6*np.sin(along*9)))**6*(.5+.5*np.cos(along*5)**2)).astype(np.float32)


def reagent_flow(shape, seed, direction, leakage, unevenness):
    """Finite reagent budget: channelled spread, side escape and a ragged front.

    Returns remaining reagent, escaped reagent and a residue-density proxy.
    Their spatial means account for one unit of initial reagent per pixel.
    """
    y,x=coordinates(shape)
    along=y if direction in ("down","up") else x
    across=x if direction in ("down","up") else y
    if direction in ("up","left"):along=1-along
    rng=np.random.default_rng(np.random.SeedSequence([seed,411]))
    field=clouds(shape,seed,412,cells=7)
    lanes=np.zeros(shape,np.float32)
    for center in (.17,.5,.83):
        bend=.025*np.sin(along*rng.uniform(3,7)+rng.uniform(0,6))
        lanes+=np.exp(-((across-center-bend)/rng.uniform(.09,.16))**2)
    # Late portions of a poorly supplied channel are never reached by the paste.
    front=1-unevenness*(.10+.30*(1-np.clip(lanes,0,1))+.12*field)
    if unevenness:front+=unevenness*.025*(clouds(shape,seed,413,cells=31)-.5)
    reached=np.clip((front-along)/.025+.5,0,1) if unevenness else np.ones(shape,np.float32)
    quantity=(1+unevenness*(lanes-.7)) * reached
    # A finite-width escape path reaches inward from one side and snakes with travel.
    side=across if rng.random()<.5 else 1-across
    width=.025+.15*leakage*(.35+.65*field)
    escape=leakage*np.exp(-side/width)*(.3+.7*along)
    escaped=quantity*np.clip(escape,0,1)
    remaining=quantity-escaped
    # Redistribute the original unit budget; losses never create extra reagent.
    scale=1/max(float(quantity.mean()),1e-6)
    remaining*=scale;escaped*=scale
    residue=escaped*(.4+1.5*field)
    return remaining.astype(np.float32),escaped.astype(np.float32),residue.astype(np.float32)


def polish_grain(shape,seed,angle_degrees):
    """Band-limited directional grooves with nonperiodic spacing and pressure."""
    y,x=coordinates(shape);a=math.radians(angle_degrees)
    across=x*math.cos(a)+y*math.sin(a)
    along=-x*math.sin(a)+y*math.cos(a)
    rng=np.random.default_rng(np.random.SeedSequence([seed,501]))
    lines=np.zeros(shape,np.float32)
    max_frequency=max(1,min(220,min(shape)/4))
    for _ in range(9):
        frequency=rng.uniform(30,220)
        attenuation=math.exp(-(frequency/max_frequency)**4)
        lines+=attenuation*np.sin(2*np.pi*(across*frequency+.12*np.sin(along*rng.uniform(3,9)))+rng.uniform(0,6.28))/9
    pressure=.4+.6*clouds(shape,seed,502)
    return (lines*pressure).astype(np.float32),clouds(shape,seed,503,cells=45)
