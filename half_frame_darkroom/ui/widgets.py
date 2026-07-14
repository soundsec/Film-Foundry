"""Small reusable Tkinter widgets shared by Film Foundry tools."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class VerticalScrolledFrame(ttk.Frame):
    """A themed frame with a vertical scrollbar and resize-aware content.

    Use ``content`` as the parent for child controls, then call
    ``bind_mousewheel()`` after populating it.  Keeping the canvas plumbing in a
    shared component avoids each material/process editor implementing subtly
    different scrolling behavior.
    """

    def __init__(self, parent, *, canvas_width: int = 500, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            width=int(canvas_width),
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        self.content = ttk.Frame(self.canvas, padding=(4, 2, 8, 8))
        self._window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self._scroll_update_id: str | None = None
        self._width_update_id: str | None = None
        self._pending_width = 0
        self._applied_width = 0
        self._pending_content_size: tuple[int, int] | None = None
        self._applied_content_size: tuple[int, int] | None = None
        self._last_scroll_region: tuple[int, int, int, int] | None = None
        self.content.bind("<Configure>", self._update_scroll_region, add="+")
        self.canvas.bind("<Configure>", self._fit_content_width, add="+")

    def _update_scroll_region(self, event=None) -> None:
        # Content can emit many Configure events while the top-level window is
        # being dragged/resized.  Windows can also repeat Configure events whose
        # size did not change while a top-level is merely moving.  Ignore those
        # events and keep at most one callback queued; repeatedly cancelling and
        # recreating ``after`` callbacks can itself starve native WM_MOVE events.
        if event is not None:
            size = (max(int(event.width), 1), max(int(event.height), 1))
            if size == self._pending_content_size:
                return
            self._pending_content_size = size
        if self._scroll_update_id is None:
            self._scroll_update_id = self.after(35, self._apply_scroll_region)

    def _apply_scroll_region(self) -> None:
        self._scroll_update_id = None
        if self._pending_content_size == self._applied_content_size:
            return
        bbox = self.canvas.bbox("all")
        if bbox is not None and bbox != self._last_scroll_region:
            self.canvas.configure(scrollregion=bbox)
            self._last_scroll_region = bbox
        self._applied_content_size = self._pending_content_size

    def _fit_content_width(self, event) -> None:
        width = max(int(event.width), 1)
        if width == self._pending_width:
            return
        self._pending_width = width
        # Do not relayout the entire long form for every intermediate Windows
        # move/resize event. Apply the latest width at a bounded rate, with only
        # one callback ever waiting in Tk's event queue.
        if self._width_update_id is None:
            self._width_update_id = self.after(50, self._apply_content_width)

    def _apply_content_width(self) -> None:
        self._width_update_id = None
        if self._pending_width != self._applied_width:
            self.canvas.itemconfigure(self._window_id, width=self._pending_width)
            self._applied_width = self._pending_width

    def _on_mousewheel(self, event) -> str:
        delta = getattr(event, "delta", 0)
        if delta:
            units = -1 if delta > 0 else 1
        else:
            units = -1 if getattr(event, "num", 0) == 4 else 1
        self.canvas.yview_scroll(units, "units")
        return "break"

    def bind_mousewheel(self) -> None:
        """Bind wheel events to the canvas and all existing descendants."""
        stack = [self.canvas, self.content]
        while stack:
            widget = stack.pop()
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_mousewheel, add="+")
            stack.extend(widget.winfo_children())

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)
