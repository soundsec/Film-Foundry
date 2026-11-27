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


def has_frame_code(border):
    return border.enabled and border.factory_marks and border.factory_barcode in {
        "code128c", "blocks", "staggered_blocks", "paired_blocks"}


def needs_strip_index(border):
    return has_frame_code(border) or (border.enabled and border.format_key=="half_frame"
                                      and border.factory_repeat=="stock_frame")


def numbered_border(border, index, *, actual_slot=False):
    from dataclasses import replace
    if border.frame_number is not None:
        return replace(border, frame_number=index if actual_slot else border.frame_number + index - 1)
    if needs_strip_index(border):
        return replace(border, strip_frame_index=index)
    return border
