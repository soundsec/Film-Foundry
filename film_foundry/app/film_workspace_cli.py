"""Thin CLI commands over the film workspace and normal batch APIs."""
import json
from pathlib import Path


def add_parser(subparsers):
    parser = subparsers.add_parser('film-work', help='Portable film source/recipe work, not a mid-bath checkpoint.')
    actions = parser.add_subparsers(dest='film_work_action', required=True)
    save = actions.add_parser('save', help='Copy ordered original sources with a complete saved session.')
    save.add_argument('--session', type=Path, required=True)
    save.add_argument('--sources', type=Path, nargs='+', required=True)
    save.add_argument('--output', type=Path, required=True)
    save.add_argument('--active', type=int, default=0, help='Zero-based active source index.')
    show = actions.add_parser('show', help='Read the saved recipe, source order and operation.')
    show.add_argument('workspace', type=Path)
    run = actions.add_parser('run', help='Execute the saved operation through the normal workflow API.')
    run.add_argument('workspace', type=Path)
    run.add_argument('--output', type=Path, required=True)
    resume = actions.add_parser('resume', help='Continue a recorded run without changing its recipe or destinations.')
    resume.add_argument('manifest', type=Path)


def execute(args):
    from film_foundry.app.film_workspace import snapshot_film_workspace, save_film_workspace, load_film_workspace, start_film_workspace_run
    from film_foundry.model.session import load_session
    from film_foundry.app.cli import _cli_control, _install_sigint_cancel
    if args.film_work_action == 'save':
        work = snapshot_film_workspace(load_session(args.session), args.sources, active=args.active)
        print(save_film_workspace(args.output, work)); return 0
    if args.film_work_action == 'show':
        work = load_film_workspace(args.workspace)
        print(json.dumps({'session': work.session, 'sources': list(work.source_names),
            'active': work.active, 'layout': work.layout}, ensure_ascii=False, indent=2)); return 0
    flag, control = _cli_control(args)
    restore_sigint = _install_sigint_cancel(flag)
    try:
        if args.film_work_action == 'run':
            manifest, result = start_film_workspace_run(load_film_workspace(args.workspace), args.output, control=control)
        else:
            from film_foundry.app.replay_batch import load_resume_plan, run_resume_batch
            manifest = args.manifest
            result = run_resume_batch(load_resume_plan(manifest), control=control)
    finally:
        restore_sigint()
    print(json.dumps({'manifest': str(manifest), 'completed': result.completed, 'reused': result.reused,
        'failures': [{'source': str(p), 'error': str(e)} for p, e in result.failures],
        'cancelled': result.cancelled, 'record_error': str(result.record_error) if result.record_error else None},
        ensure_ascii=False, indent=2))
    return 1 if result.failures or result.cancelled or result.record_error else 0
