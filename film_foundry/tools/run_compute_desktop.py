"""Explicit optional GPU launcher; CPU fallback remains available."""
import argparse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compute',choices=('cpu','cuda','opencl','auto'))
    parser.add_argument('--material-threads',type=int,help='CPU workers for independent material rows and channels; 0 selects automatically.')
    parser.add_argument('--diagnose',action='store_true',help='Probe the selected backend and print status without opening the GUI')
    args=parser.parse_args()
    from film_foundry.core.material_parallel import configure
    try:configure(backend=args.compute,threads=args.material_threads)
    except ValueError as exc:parser.error(str(exc))
    if args.diagnose:
        import os
        import json
        import numpy as np
        from film_foundry.core.desktop_compute import try_dye, status
        from film_foundry.app.desktop_settings import initialize_compute
        initialize_compute()
        requested = os.environ['FILM_FOUNDRY_COMPUTE'].lower()
        try_dye(np.zeros((512,512,3),np.float32),np.eye(3),np.zeros(3),0.,True)
        if requested != 'cpu':
            from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
            from film_foundry.core.spectral_exposure import map_profile_exposure
            from film_foundry.core.film_process.integration import _exposure_material
            from film_foundry.core.development_compute import try_conversion, try_reversal_dye
            from film_foundry.model.config import FilmStockConfig
            neutral = np.full((512, 512, 3), .18, np.float32)
            rates = np.ones(3, np.float32)
            try_conversion(neutral, neutral, rates, rates, None, None, 0., 0., -.1)
            map_profile_exposure(neutral, make_smits_exposure_profile(1))
            stock = FilmStockConfig()
            _exposure_material(stock).expose(neutral)
            try_reversal_dye(neutral, stock)
        print(json.dumps(status(),ensure_ascii=False,indent=2))
        return
    from film_foundry.tools.run_desktop_gui import main as desktop
    desktop()


if __name__=='__main__':main()
