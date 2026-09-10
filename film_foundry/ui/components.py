"""Reusable form components shared by Film Foundry's Qt tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from film_foundry.ui.qt import ObservableValue, ui


@dataclass(frozen=True)
class NumericControl:
    label: ui.Label
    slider: ui.Slider
    number_input: ui.NumberInput


def add_numeric_control(
    parent: ui.Frame,
    *,
    label: str,
    value: ObservableValue,
    minimum: float,
    maximum: float,
    row: int,
    label_column: int = 0,
    slider_column: int = 1,
    slider_span: int = 2,
    input_column: int = 3,
    increment: float | None = None,
    input_width: int = 9,
    on_change: Callable[[], None] | None = None,
    pady: int = 4,
) -> NumericControl:
    """Add one bound slider with an exact numeric editor.

    The observable value is the only synchronization source. A change callback
    is attached once instead of separately to both native widgets.
    """

    label_widget = ui.Label(parent, text=label)
    label_widget.grid(row=row, column=label_column, sticky="w", pady=pady)
    slider = ui.Slider(parent, from_=minimum, to=maximum, variable=value)
    slider.grid(
        row=row,
        column=slider_column,
        columnspan=slider_span,
        sticky="ew",
        pady=pady,
    )
    step = increment
    if step is None:
        step = max((maximum - minimum) / 200.0, 0.000001)
    number_input = ui.NumberInput(
        parent,
        from_=minimum,
        to=maximum,
        increment=step,
        textvariable=value,
        width=input_width,
        justify="right",
    )
    number_input.grid(row=row, column=input_column, sticky="e", pady=pady)
    parent.columnconfigure(slider_column, weight=1)
    if on_change is not None:
        value.trace_add("write", lambda *_: on_change())
    return NumericControl(label_widget, slider, number_input)


def add_choice_control(
    parent: ui.Frame,
    *,
    label: str,
    value: ObservableValue,
    choices: Iterable[str],
    row: int,
    on_change: Callable[[], None] | None = None,
) -> ui.Choice:
    """Add a read-only choice bound to one observable controller value."""

    ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
    choice = ui.Choice(
        parent,
        textvariable=value,
        values=tuple(choices),
        state="readonly",
    )
    choice.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
    if on_change is not None:
        value.trace_add("write", lambda *_: on_change())
    return choice
