"""A disposable forecast of one exposed frame; never develops the roll."""
from copy import deepcopy
import numpy as np
from film_foundry.app.electronic_roll import RollSession
from film_foundry.app.darkroom_process import condition_driven_config
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.app.film_test_strip import FilmStripSession
from film_foundry.app.control import WorkflowCancelled


class RollPeek:
    def __init__(self,path,config):
        session=RollSession(path)
        if session.data['phase']!='sealed':raise ValueError('The roll has started; its chemistry is frozen')
        frame=next(f for f in session.data['roll']['frames'] if f['state']=='latent')
        self.frame_index=frame['frame_index'];self.roll_path=session.path
        self.revision=session.path.stat().st_mtime_ns
        self.config=prepare_incident_config(condition_driven_config(session.process_config(config)),roll_id=session.data['roll']['roll_id'])
        self.source=session.path.parent/frame['file']

    def render(self,cancel):
        from film_foundry.core.latent_io import load_exposed_latent
        from film_foundry.app.preview_inputs import preview_latent
        from film_foundry.core.engine import develop_negative_from_latent,scan_medium_output
        if cancel.requested():raise WorkflowCancelled()
        probe=FilmStripSession(self.source,'develop_latent',self.config,long_edge=640)
        config=deepcopy(self.config);config.seed_strategy='fixed';config.random_seed=probe.seed
        latent,_=load_exposed_latent(self.source)
        medium=develop_negative_from_latent(preview_latent(latent,640),config,rng=np.random.default_rng(probe.seed))
        if cancel.requested():raise WorkflowCancelled()
        image=scan_medium_output(medium,config,config.scanner.interpretation_mode).output_srgb
        probe.check_source()
        if self.roll_path.stat().st_mtime_ns!=self.revision:raise ValueError('Roll changed; reopen the preview')
        if not np.isfinite(image).all():raise ValueError('Non-finite preview')
        return image
