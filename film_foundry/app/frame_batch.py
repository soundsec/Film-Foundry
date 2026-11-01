"""Per-item identity for frontends using the existing frame identity contract."""

from film_foundry.core.frame_identity import normalize_frame_identity


def frame_identity_for_item(settings, item_index, item_count):
    value = normalize_frame_identity((settings or {}).get("frame_identity"))
    if value is None:
        return None
    if item_count > 1 and "frame_id" in value:
        raise ValueError("A fixed frame_id requires a single input; use starting frame_index for a batch.")
    if "frame_index" in value:
        value["frame_index"] += item_index
    return value
