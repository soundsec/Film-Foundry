"""Reusable configuration inputs; definitions and processing stay in public APIs."""

from film_foundry.app.config_assets import load_profile_file, load_curve_file
from film_foundry.model.config import normalize_scanner_color_curve_rgb
from film_foundry.ui.i18n import tr
from film_foundry.ui.qt import file_dialog, ui


class OptionalNumberField:
    def __init__(self, parent, label, *, row=0, integer=False):
        self.value = ui.TextValue(value="")
        self.integer = integer
        ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        self.entry = ui.TextInput(parent, textvariable=self.value)
        self.entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)

    def load(self, value):
        self.value.set("" if value is None else str(value))

    def read(self):
        value = self.value.get().strip()
        return None if not value else int(value) if self.integer else float(value)


class NumberListField:
    def __init__(self, parent, label, *, row=0):
        self.value = ui.TextValue(value="")
        ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ui.TextInput(parent, textvariable=self.value).grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)

    def load(self, value):
        self.value.set("; ".join(repr(float(item)) for item in value))

    def read(self):
        return tuple(float(item.strip()) for item in self.value.get().split(";") if item.strip())


class ProfileField:
    def __init__(self, parent, key, *, row=0, on_change=None):
        self.key, self.profile, self.on_change = key, None, on_change
        self.label = ui.TextValue(value=tr("profile.disabled"))
        ui.Label(parent, text=tr("profile." + key)).grid(row=row, column=0, sticky="w", pady=3)
        ui.Label(parent, textvariable=self.label, wraplength=360).grid(row=row, column=1, sticky="ew", pady=3)
        ui.Button(parent, text=tr("button.load_profile"), command=self.choose).grid(row=row, column=2)
        ui.Button(parent, text=tr("button.clear_profile"), command=lambda: self.load(None)).grid(row=row, column=3)
        self.parent = parent

    def load(self, profile):
        self.profile = profile
        if profile is None:
            caption = tr("profile.disabled")
        elif "smits" in str(getattr(profile, "model", "")):
            caption = tr("profile.smits_on")
        else:
            caption = profile.profile_id
        self.label.set(caption)
        if self.on_change is not None:
            self.on_change()

    def choose(self):
        path = file_dialog.askopenfilename(parent=self.parent, filetypes=[("Profile / recipe JSON", "*.json")])
        if path:
            try:
                self.load(load_profile_file(path, self.key))
            except Exception as exc:
                from film_foundry.ui.qt import message_box
                message_box.showerror(tr("status.profile_failed"), str(exc), parent=self.parent)


class RGBCurveField:
    """Three editable piecewise curves; no alternative curve mathematics."""
    def __init__(self, parent, key, *, row=0):
        self.key, self.parent = key, parent
        self.enabled = ui.BoolValue(value=False)
        self.values = [ui.TextValue(value="0:0; 1:1") for _ in range(3)]
        self.entries = []
        ui.Toggle(parent, text=tr("curve." + key), variable=self.enabled, command=self._sync).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        ui.Button(parent, text=tr("button.load_profile"), command=self.choose).grid(row=row, column=2)
        ui.Button(parent, text=tr("curve.identity"), command=lambda: self.load((((0., 0.), (1., 1.)),) * 3)).grid(row=row, column=3)
        for channel, value in enumerate(self.values):
            ui.Label(parent, text="RGB"[channel] + " · x:y").grid(row=row+channel+1, column=0, sticky="w")
            entry = ui.TextInput(parent, textvariable=value)
            entry.set_tooltip(tr("curve.help"))
            entry.grid(row=row+channel+1, column=1, columnspan=3, sticky="ew", pady=2)
            self.entries.append(entry)
        self._sync()

    def _sync(self):
        for entry in self.entries:
            entry.configure(state="normal" if self.enabled.get() else "disabled")

    def load(self, value):
        self.enabled.set(value is not None)
        curves = value if value is not None else (((0., 0.), (1., 1.)),) * 3
        for text, curve in zip(self.values, curves):
            text.set("; ".join(f"{float(x)!r}:{float(y)!r}" for x, y in curve))
        self._sync()

    def read(self):
        if not self.enabled.get():
            return None
        curves = []
        for text in self.values:
            points = [tuple(float(v.strip()) for v in part.split(":")) for part in text.get().split(";") if part.strip()]
            curves.append(points)
        return normalize_scanner_color_curve_rgb(curves, field_name=f"scanner.{self.key}")

    def choose(self):
        path = file_dialog.askopenfilename(parent=self.parent, filetypes=[("RGB curve / recipe JSON", "*.json")])
        if path:
            try:
                self.load(load_curve_file(path, self.key))
            except Exception as exc:
                from film_foundry.ui.qt import message_box
                message_box.showerror(tr("status.profile_failed"), str(exc), parent=self.parent)


def add_apply_button(parent, editor, *, row):
    if editor._on_apply is not None:
        ui.Button(parent, text=tr("button.apply_current"), command=lambda: apply_current(editor)).grid(row=row, column=0, columnspan=4, sticky="ew", pady=5)


class GraphCurveField:
    """Form adapter shared by independent scanner editor and workbench."""
    def __init__(self, parent, key, *, row=0):
        from film_foundry.ui.curve_editor import CurveEditor
        self.frame = ui.Frame(parent)
        self.frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=6)
        self.editor = CurveEditor(key, self.frame.qt)
        self.editor.setMinimumHeight(330)
        self.frame.qt.setMinimumHeight(330)
        self.frame._ensure_pack_layout().addWidget(self.editor)

    def load(self, value):
        self.editor.load(value)

    def read(self):
        return self.editor.read()


def apply_current(editor):
    try:
        editor._on_apply(editor._config_from_controls())
        editor.status.set(tr("status.applied_current"))
    except Exception as exc:
        editor.status.set(f"{tr('status.apply_failed')}: {exc}")
