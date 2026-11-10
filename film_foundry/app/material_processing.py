"""Processing facts derived from the actual material, never from a GUI draft."""
import json


def processing_info(session, body_index=0):
    state, group = session.state, session.group
    body = group.bodies[body_index] if group is not None else None
    fields = state.fields if state is not None else body.fields if body is not None else {}
    image = next(iter(fields.values())) if fields else session.image
    process = json.loads(state.process_json) if state is not None else json.loads(group.provenance_json).get('process', {}) if group is not None else {}
    source = process.get('source_input', session.source_info)
    precision = process.get('storage_precision') or (json.loads(group.provenance_json).get('storage_precision') if group else None)
    return {'experimental': True, 'family': session.kind, 'quality': 'native_full',
            'compute_dtype': 'float32', 'storage_precision':precision, 'working_size': list(image.shape[1::-1]) if image is not None else None,
            'source_size': source.get('source_size'), 'formation_long_edge': source.get('formation_long_edge'),
            'source_name': session.source_name, 'stage': state.stage if state is not None else 'final' if group is not None else 'source',
            'material_version': state.schema_version if state is not None else body.schema_version if body is not None else None,
            'seed': state.seed if state is not None else None,
            'body_role': body.role if body is not None else None}
