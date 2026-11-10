"""Independent experimental material lab; existing film CLI and GUI are untouched.

python -m film_foundry.tools.run_material_lab image.png --output new_directory
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
from PIL import Image, ImageOps
from film_foundry.app import material_families as api
from film_foundry.core.peel_apart import FAMILY, PeelMaterial
from film_foundry.core import integral_instant, silver_plate
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core.color import srgb_to_linear, linear_to_srgb


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image",type=Path)
    parser.add_argument("--output",type=Path,required=True,help="New directory; existing directories are refused")
    parser.add_argument("--family",choices=("peel","integral","plate"),default="peel")
    parser.add_argument("--gilding",type=float,default=0)
    parser.add_argument("--roughness",type=float,default=.12)
    parser.add_argument("--view-angle",type=float,default=25,help="Plate viewing angle in degrees")
    parser.add_argument("--transfer",choices=("dye","silver_compound"),default="dye")
    parser.add_argument("--seconds",type=float,default=60,help="Peel/development time; integral intermediate snapshot before stabilization")
    parser.add_argument("--stabilization-seconds",type=float,default=300,help="Integral neutralization/completion time")
    parser.add_argument("--temperature",type=float,default=20)
    parser.add_argument("--coverage",type=float,default=1)
    parser.add_argument("--contact",type=float,default=1)
    parser.add_argument("--streaks",type=float,default=0)
    parser.add_argument("--edge-loss",type=float,default=0)
    parser.add_argument("--direction",choices=("down","up","left","right"),default="down")
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--border",choices=("none","equal","pod"),default="none",help="Optional paper border; pod follows a short edge")
    args=parser.parse_args(argv)
    if args.output.exists():parser.error("Output directory already exists")
    with Image.open(args.image) as source:
        pixels=np.asarray(ImageOps.exif_transpose(source).convert("RGB"),dtype=np.float32)/255
    # This first lab accepts display sRGB images; RAW/HDR and ICC conversion are separate I/O work.
    import math
    if not math.isfinite(args.view_angle) or not -89<=args.view_angle<=89:parser.error("View angle must be -89..89")
    if not math.isfinite(args.gilding) or not 0<=args.gilding<=1:parser.error("Gilding must be 0..1")
    kind={"peel":FAMILY,"integral":integral_instant.FAMILY,"plate":silver_plate.FAMILY}[args.family]
    engine=api.family(kind)
    material=(PeelMaterial(transfer=args.transfer) if args.family=="peel" else
              integral_instant.InstantMaterial(transfer=args.transfer,stabilization_seconds=args.stabilization_seconds) if args.family=="integral" else
              silver_plate.PlateMaterial(roughness=args.roughness))
    exposed=engine.expose(srgb_to_linear(pixels),material,group_id=str(uuid4()),seed=args.seed)
    if args.family=="plate":
        state=engine.process(exposed,"develop",seconds=args.seconds)
        final=engine.process(state,"fix")
        if args.gilding:final=engine.process(final,"gild",amount=args.gilding)
    else:
        start=engine.process(exposed,"eject") if args.family=="integral" else exposed
        state=engine.process(start,"spread",temperature_c=args.temperature,coverage=args.coverage,contact=args.contact,
                             roller_streaks=args.streaks,edge_loss=args.edge_loss,direction=args.direction)
        state=engine.process(state,"advance",seconds=args.seconds)
        final=(engine.process(state,"peel") if args.family=="peel" else
               state if state.stage=="stable" else engine.process(state,"stabilize"))
    group=engine.finish(final)
    args.output.mkdir(parents=True,exist_ok=False)
    api.save(args.output/"processing.ffmaterial.npz",state)
    api.save(args.output/"final_state.ffmaterial.npz",final)
    api.save(args.output/"products.ffmaterial.npz",group)
    radians=math.radians(args.view_angle)
    conditions=ViewingConditions(view_direction=(math.sin(radians),0.,math.cos(radians))) if args.family=="plate" else ViewingConditions(include_border=args.border!="none",border_style="pod_short" if args.border=="pod" else args.border)
    for body in group.bodies:
        api.save_body(args.output/(body.role+".ffmaterial.npz"),group,body.body_id)
        rendered=np.clip(linear_to_srgb(api.observe(body,conditions)),0,1)
        Image.fromarray((rendered*255+.5).astype(np.uint8)).save(args.output/(body.role+".png"))
    (args.output/"view.json").write_text(json.dumps(dict(group_id=group.group_id,conditions=asdict(conditions),
        encoding="sRGB PNG 8-bit",source="display_srgb_input",experimental=True),indent=2),encoding="utf-8")
    print(f"Created experimental {args.family} products: {args.output}")


if __name__=="__main__":main()
