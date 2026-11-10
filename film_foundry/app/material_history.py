"""Immutable material snapshots; undo never deletes already exported files."""
from copy import deepcopy
from .edit_actions import SnapshotHistory

SHARED=('image','state','group','optical_source')
VALUES=('source_name','source_path','source_info','optical_body_id','optical_relay','optical_reference','scan_adjustments','storage_precision')


def capture(session):
    return {**{key:getattr(session,key) for key in SHARED},**{key:deepcopy(getattr(session,key)) for key in VALUES}}


def changed(a,b):
    return any(a[key] is not b[key] for key in SHARED) or any(a[key]!=b[key] for key in VALUES)


def restore(session,snapshot):
    for key in SHARED:setattr(session,key,snapshot[key])
    for key in VALUES:setattr(session,key,deepcopy(snapshot[key]))
    session.__dict__.pop('_result_archive_cache',None)


def new_history(session):
    # Bound retained full-size formation states. Arrays are immutable and shared;
    # use a short history rather than silently copying a large image per edit.
    return SnapshotHistory(capture(session),limit=4)
