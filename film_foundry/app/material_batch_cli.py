"""Reference CLI for the same independent-material batch coordinator."""
import json
from pathlib import Path


def add_parser(subparsers):
    parser=subparsers.add_parser('material-batch',help='Run or resume an independent-material recipe on ordered image sources.')
    actions=parser.add_subparsers(dest='material_batch_action',required=True)
    run=actions.add_parser('run');run.add_argument('--recipe',type=Path,required=True)
    run.add_argument('--sources',type=Path,nargs='+',required=True);run.add_argument('--output',type=Path,required=True)
    run.add_argument('--work-long-edge',type=int,default=0)
    resume=actions.add_parser('resume');resume.add_argument('manifest',type=Path)


def execute(args):
    from film_foundry.app.material_batch_run import create_batch,run_batch
    from film_foundry.app.material_recipes import load_recipe,bind_recipe
    from film_foundry.core.material_family import ViewingConditions
    from film_foundry.app.cli import _cli_control,_install_sigint_cancel
    if args.material_batch_action=='run':
        recipe=load_recipe(args.recipe);kind,_=bind_recipe(recipe)
        manifest=create_batch(kind,args.sources,args.output,recipe['material'],
            [(s['operation'],s['parameters']) for s in recipe['steps']],ViewingConditions(),
            args.work_long_edge,recipe['seed'],material_version=recipe['material_version'])
    else:manifest=args.manifest
    flag,control=_cli_control(args);restore=_install_sigint_cancel(flag)
    try:report=run_batch(manifest,control=control)
    finally:restore()
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if report['failures'] or report['cancelled'] or report['notification_errors'] else 0
