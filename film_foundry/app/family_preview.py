"""Diagnostic observations through each material family's own reference process."""
from dataclasses import asdict
import json
from film_foundry.app.diagnostic_targets import diagnostic_target
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.core.color import srgb_to_linear
from film_foundry.app.material_reference import reference_view


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
            session.run_standard(asdict(definition), seed=13579)
            self.session, self.source, self.key = session, source, key
        view = reference_view(self.kind, **config.get('view', {}))
        from film_foundry.app.material_scan import normalize_adjustments
        self.session.scan_adjustments=normalize_adjustments(config.get('scan_adjustments'))
        return self.source.copy(), self.session.preview(view), reused
