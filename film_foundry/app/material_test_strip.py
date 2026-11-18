"""Isolated trials through each material's existing session operations."""
from copy import deepcopy
import numpy as np
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.material_history import capture,restore
from film_foundry.app.control import WorkflowCancelled,WorkflowControl
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core.preview import resize_to_long_edge


class MaterialStripSession:
    def __init__(self,source,*,long_edge=640,reuse=True):
        self.kind=source.kind;self.snapshot=capture(source);self.long_edge=long_edge
        self.reuse=reuse;self._formed_key=None;self._formed=None;self._source=None;self._cancel=None

    def clear(self):
        self._formed_key=None;self._formed=None;self._source=None

    def check_source(self):
        # Source is an in-memory immutable material snapshot, never a file reload.
        pass

    def render(self,config):
        from film_foundry.core.computation_control import checkpoint
        def cancelled():
            checkpoint()
            return self._cancel is not None and self._cancel.requested()
        control=WorkflowControl(cancel_requested=cancelled)
        control.check_cancel()
        session=MaterialLabSession(self.kind);restore(session,self.snapshot)
        fresh=session.state is None and session.group is None
        operation=config['operation']
        # A single bounded formed result. Observation choices never own chemistry;
        # changes to any formation input invalidate it before allocating another.
        key=(config['material'],config['seed'],config['operations']) if fresh else (operation,config['operations'].get(operation))
        if self.reuse and self._formed is not None and key==self._formed_key:
            restore(session,self._formed)
        elif fresh:
            self._formed=None;self._formed_key=None
            if session.image is None:raise ValueError('Load a source before making material trials')
            if self.long_edge is not None:
                if self._source is None:self._source=resize_to_long_edge(session.image,self.long_edge).copy()
                session.image=self._source
            session.source_info=deepcopy(session.source_info)
            session.source_info['working_size']=list(session.image.shape[1::-1])
            session.run_standard(config['material'],config['seed'],config['operations'],control=control)
        elif operation is not None:
            self._formed=None;self._formed_key=None
            session.process(operation,config['operations'][operation])
        control.check_cancel()
        if self.reuse and key!=self._formed_key:
            self._formed=capture(session);self._formed_key=deepcopy(key)
        session.scan_adjustments=deepcopy(config['scan'])
        image=session.preview(ViewingConditions(**config['view']),config['body'])
        if image is None or not np.isfinite(image).all():raise ValueError('No finite material trial result')
        return resize_to_long_edge(image,self.long_edge)

    def formal_frame(self,config,*,scaled_override=False):
        from copy import copy
        # The work size is already represented by the retained exposure or state.
        native=copy(self)
        native.clear()
        native.long_edge=None
        native.reuse=False
        return native.render(config)

    def run(self,variants,cancel,on_result):
        self._cancel=cancel
        from film_foundry.app.control import WorkflowControl
        control = WorkflowControl(cancel_requested=cancel.requested)
        try:
            for index,(value,config) in enumerate(variants):
                if cancel.requested():raise WorkflowCancelled()
                pixels=preview_budget.run(Priority.STRIP, self.render, config, check=control.check_cancel)
                if cancel.requested():raise WorkflowCancelled()
                on_result(index,value,pixels)
        finally:self.clear();self._cancel=None
