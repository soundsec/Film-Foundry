"""Diagnostic observations through each material family's own reference process."""
from dataclasses import asdict
import json
from film_foundry.app.diagnostic_targets import diagnostic_target
from film_foundry.app.material_lab_session import MaterialLabSession, SHEET_FAMILIES
from film_foundry.core.color import srgb_to_linear
from film_foundry.core.material_family import ViewingConditions


class FamilyPreviewSession:
    def __init__(self, kind):
        self.kind = kind
        self.key = None
        self.session = None
        self.source = None

    def render_target(self, target, config):
        material = config.get('material', {})
        key = json.dumps([target, material], sort_keys=True, allow_nan=False)
        reused = key == self.key
        if not reused:
            source = diagnostic_target(target)
            session = MaterialLabSession(self.kind)
            session.image = srgb_to_linear(source.copy())
            definition = session.material_type(**material)
            if self.kind in SHEET_FAMILIES:
                session.run_standard(asdict(definition), seed=13579)
            else:
                if self.kind == 'plate':
                    steps = [('prepare', {}), ('polish', {}), ('sensitize', {}),
                             ('expose', {}), ('develop', {'seconds': definition.reaction_seconds * 4}),
                             ('fix', {'seconds': definition.fixing_seconds * 12})]
                else:
                    steps = [('expose', {})]
                    if self.kind == 'integral':
                        steps.append(('eject', {}))
                    steps.extend([('spread', {}), ('advance', {'seconds':
                        definition.stabilization_seconds * 2 if self.kind == 'integral'
                        else definition.reaction_seconds * 3})])
                    steps.append(('stabilize' if self.kind == 'integral' else 'peel', {}))
                for operation, parameters in steps:
                    if operation == 'stabilize' and session.state.stage == 'stable':continue
                    session.process(operation, parameters, asdict(definition), seed=13579)
            self.session, self.source, self.key = session, source, key
        view = ViewingConditions(**config.get('view', {}))
        from film_foundry.app.material_scan import normalize_adjustments
        self.session.scan_adjustments=normalize_adjustments(config.get('scan_adjustments'))
        return self.source.copy(), self.session.preview(view), reused
