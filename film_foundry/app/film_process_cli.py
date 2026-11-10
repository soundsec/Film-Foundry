"""CLI reference frontend for frozen-program reaction continuation."""
import json
from pathlib import Path


def add_parser(subparsers):
    parser = subparsers.add_parser('film-process', help='Pause/resume frozen film reactions from a latent; no mid-bath editing.')
    actions = parser.add_subparsers(dest='film_process_action', required=True)
    start = actions.add_parser('start')
    start.add_argument('input', type=Path); start.add_argument('output', type=Path)
    start.add_argument('--session', type=Path, required=True, help='Saved film session with complete process settings.')
    start.add_argument('--stop-after', type=int, default=0, help='Completed reaction step count; 0 prepares the frozen program.')
    resume = actions.add_parser('continue')
    resume.add_argument('input', type=Path); resume.add_argument('output', type=Path)
    resume.add_argument('--stop-after', type=int, help='Absolute reaction boundary; omit to finish into a developed medium.')
    resume.add_argument('--rebuild-preparation', action='store_true', help='Recompute derived spatial fields from the same frozen inputs; preserves saved chemical inventories.')
    revise = actions.add_parser('revise', help='Save new conditions for eligible unentered baths, without advancing reactions.')
    revise.add_argument('input',type=Path);revise.add_argument('output',type=Path)
    revise.add_argument('--baths',type=Path,required=True,help='JSON array of explicit bath overrides; source bath numbers/actions stay fixed.')
    show = actions.add_parser('show'); show.add_argument('input', type=Path)


def execute(args):
    from film_foundry.app.film_process_work import start_work, continue_work, describe_work
    from film_foundry.app.cli import _cli_control, _install_sigint_cancel
    from film_foundry.app.control import WorkflowCancelled
    if args.film_process_action == 'show':
        print(json.dumps(describe_work(args.input), ensure_ascii=False, indent=2)); return 0
    flag, control = _cli_control(args); restore = _install_sigint_cancel(flag)
    try:
        if args.film_process_action == 'start':
            from film_foundry.model.session import load_session
            from film_foundry.model.config import DarkroomConfig
            from film_foundry.app.source_seed import offset_for
            session = load_session(args.session)
            result = start_work(args.input, args.output, DarkroomConfig.from_dict(session['config']), stop_after=args.stop_after,
                control=control, path_seed_offset=offset_for(args.input, session.get('path_seed_offsets')))
        elif args.film_process_action=='revise':
            from film_foundry.app.film_process_revision import revise_work
            from film_foundry.core.atomic_io import strict_json_loads
            result=revise_work(args.input,args.output,strict_json_loads(args.baths.read_text(encoding='utf-8-sig')),control=control)
        else:
            result = continue_work(args.input, args.output, stop_after=args.stop_after, control=control,
                reuse_preparation=not args.rebuild_preparation)
    except WorkflowCancelled:
        print('Cancelled before a reaction checkpoint was available.'); return 130
    finally:
        restore()
    print(json.dumps({'output': str(result.output_path), 'status': result.status,
        'next_step': result.next_step, 'total_steps': result.total_steps, 'artifacts': result.artifacts}, ensure_ascii=False, indent=2))
    return 130 if result.status == 'cancelled' else 0
