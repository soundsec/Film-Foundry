"""Explicit optional GPU launcher; CPU fallback remains available."""
import argparse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compute',choices=('cpu','cuda','opencl','auto'),default='cpu')
    parser.add_argument('--material-threads',type=int,help='Independent-material CPU row workers; 0 selects automatically.')
    parser.add_argument('--diagnose',action='store_true',help='Probe the selected backend and print status without opening the GUI')
    args=parser.parse_args()
    from film_foundry.core.material_parallel import configure
    try:configure(backend=args.compute,threads=args.material_threads)
    except ValueError as exc:parser.error(str(exc))
    if args.diagnose:
        import json
        import numpy as np
        from film_foundry.core.desktop_compute import try_dye, status
        from film_foundry.core.opencl_compute import available
        if args.compute=='opencl':
            available()
        else:
            try_dye(np.zeros((512,512,3),np.float32),np.eye(3),np.zeros(3),0.,True)
        print(json.dumps(status(),ensure_ascii=False,indent=2))
        return
    from film_foundry.tools.run_desktop_gui import main as desktop
    desktop()


if __name__=='__main__':main()
