"""Material workspaces in one desktop window; standalone compatibility is optional."""
import argparse
from film_foundry.ui.qt import _application
from film_foundry.ui.material_lab_window import create_material_window
from film_foundry.app.material_lab_session import FAMILIES


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family",choices=("all",*FAMILIES),default="all")
    parser.add_argument('--compute',choices=('cpu','cuda','opencl','auto'))
    parser.add_argument('--material-threads',type=int,help='CPU row workers; 0 selects automatically.')
    parser.add_argument('--standalone',action='store_true',help='Use the former independent material windows.')
    args=parser.parse_args(argv)
    from film_foundry.core.material_parallel import configure
    try:configure(backend=args.compute,threads=args.material_threads)
    except ValueError as exc:parser.error(str(exc))
    app=_application()
    kinds=tuple(FAMILIES) if args.family=="all" else (args.family,)
    if not args.standalone:
        from film_foundry.tools.run_desktop_gui import DesktopPanel
        panel=DesktopPanel();panel.open_library()
        for kind in kinds:panel._show_material_workspace(kind)
        if args.family=='all':panel.open_library()
        panel.root.qt.show()
        return app.exec()
    windows=[]
    for i,kind in enumerate(kinds):
        window=create_material_window(kind);windows.append(window)
        window.move(50+i*45,50+i*35);window.show()
    return app.exec()


if __name__=="__main__":raise SystemExit(main())
