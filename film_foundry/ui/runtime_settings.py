"""Desktop settings for existing input, resource, and output capabilities."""

import copy

from film_foundry.core.frame_identity import normalize_frame_identity
from film_foundry.ui.config_fields import add_apply_button
from film_foundry.ui.i18n import tr, current_language
from film_foundry.ui.qt import ui
from film_foundry.ui.widgets import VerticalScrolledFrame


# Explicit public fields. Private discard policies, model topology switches and
# unimplemented film rebate geometry deliberately have no settings here.
SETTINGS = (
    ("input", "input_icc_policy", "str", ("apply", "ignore"), "输入 ICC 策略", "Input ICC policy"),
    ("look", "look_strength", "float", (), "整体微调混合强度", "Overall look adjustment strength"),
    ("processing", "comfort_zone_megapixels", "float", (), "舒适区提醒 MP（不限制尺寸）", "Comfort-zone warning MP (no size limit)"),
    ("processing", "native_thread_limit", "int", (), "原生线程上限（0=库默认）", "Native thread limit (0: library default)"),
    ("processing", "memory_budget_mb", "optional_float", (), "内存预算 MiB（空=不指定）", "Memory budget MiB (blank: unspecified)"),
    ("processing", "memory_budget_policy", "str", ("allow", "warn", "error"), "预算策略", "Budget policy"),
    ("processing", "history_storage_policy", "str", ("full", "cold_fp16"), "历史阶段保留", "History storage policy"),
    ("processing", "halation_work_long_edge", "optional_int", (), "光晕工作长边（空=质量默认）", "Halation work edge (blank: quality default)"),
    ("processing", "emulsion_spread_work_long_edge", "optional_int", (), "乳剂扩散工作长边", "Emulsion spread work edge"),
    ("processing", "grain_work_long_edge", "optional_int", (), "颗粒工作长边", "Grain work edge"),
    ("processing", "adjacency_work_long_edge", "optional_int", (), "邻接显影工作长边", "Adjacency work edge"),
    ("processing", "dir_work_long_edge", "optional_int", (), "DIR 工作长边", "DIR work edge"),
    ("processing", "material_tile_rows", "int", (), "材料分块行数（0=不分块）", "Material tile rows (0: untiled)"),
    ("processing", "material_tile_threshold_megapixels", "float", (), "材料分块阈值 MP", "Material tiling threshold MP"),
    ("processing", "scan_tile_rows", "int", (), "扫描分块行数（0=不分块）", "Scan tile rows (0: untiled)"),
    ("processing", "scan_tile_threshold_megapixels", "float", (), "扫描分块阈值 MP", "Scan tiling threshold MP"),
    ("output", "bit_depth", "int", ("8", "16"), "成片位深（16 位用 PNG/TIFF）", "Output bit depth (16-bit: PNG/TIFF)"),
    ("output", "render_long_edge", "optional_int", (), "未勾边长缩放时的处理长边（空=原图）", "Processing edge when scaling unchecked (blank: original)"),
    ("output", "quality", "int", (), "JPEG/WebP 编码质量", "JPEG/WebP encoding quality"),
    ("output", "medium_npz_compression", "str", ("compressed", "store"), "介质 NPZ 压缩", "Medium NPZ compression"),
    ("output", "encode_tile_rows", "int", (), "编码分块行数（0=不分块）", "Encoding tile rows (0: untiled)"),
    ("output", "encode_tile_threshold_megapixels", "float", (), "编码分块阈值 MP", "Encoding tiling threshold MP"),
    ("output", "scanner_raw_border_min_px", "int", (), "scanner raw 最小片基边框 px", "Minimum scanner raw base border px"),
    ("output", "watermark_metadata", "bool", (), "写入来源元数据", "Write provenance metadata"),
    ("output", "watermark_negative_material", "bool", (), "介质导出来源标记", "Medium export provenance mark"),
    ("output", "watermark_scanner_raw_border", "bool", (), "scanner raw 边框来源标记", "Scanner raw border provenance mark"),
)


class RuntimeSettings:
    def __init__(self, *, parent, config, on_apply, identity=None):
        self.root = ui.Window(parent)
        self.root.title(tr("button.runtime_settings"))
        self.root.geometry("820x760")
        self._base_config = copy.deepcopy(config)
        self._on_apply = on_apply
        self.status = ui.TextValue(value=tr("runtime.help"))
        self.values = {}
        shell = ui.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        scroller = VerticalScrolledFrame(shell, canvas_width=740)
        scroller.pack(fill="both", expand=True)
        self.controls = scroller.content
        self.controls.columnconfigure(1, weight=1)
        row = 0
        for bank, key, kind, choices, zh, en in SETTINGS:
            group = config if bank == "input" else getattr(config, bank)
            value = getattr(group, key)
            variable = ui.BoolValue(value=value) if kind == "bool" else ui.TextValue(value="" if value is None else str(value))
            self.values[key] = variable
            label = zh if current_language() == "zh_CN" else en
            if kind == "bool":
                ui.Toggle(self.controls, text=label, variable=variable).grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
            else:
                ui.Label(self.controls, text=label).grid(row=row, column=0, sticky="w", pady=3)
                if choices:
                    entry = ui.Choice(self.controls, textvariable=variable, values=choices, state="readonly")
                else:
                    entry = ui.TextInput(self.controls, textvariable=variable)
                entry.grid(row=row, column=1, sticky="ew", pady=3)
            row += 1
        identity = identity or {}
        self.identity_values = {}
        for key in ("roll_id", "frame_id", "frame_index"):
            ui.Label(self.controls, text=tr("identity." + key)).grid(row=row, column=0, sticky="w", pady=3)
            value = ui.TextValue(value=str(identity.get(key, "")))
            self.identity_values[key] = value
            ui.TextInput(self.controls, textvariable=value).grid(row=row, column=1, sticky="ew", pady=3)
            row += 1
        ui.Label(self.controls, text=tr("identity.help"), wraplength=680).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        bar = ui.Frame(shell)
        bar.pack(fill="x")
        add_apply_button(bar, self, row=0)
        ui.Label(shell, textvariable=self.status, wraplength=760).pack(fill="x")

    def read_identity(self):
        payload = {key: value.get().strip() for key, value in self.identity_values.items() if value.get().strip()}
        if "frame_index" in payload:
            payload["frame_index"] = int(payload["frame_index"])
        return normalize_frame_identity(payload)

    def _config_from_controls(self):
        config = copy.deepcopy(self._base_config)
        for bank, key, kind, _choices, _zh, _en in SETTINGS:
            raw = self.values[key].get()
            if kind == "bool":
                value = bool(raw)
            elif kind.startswith("optional_") and not raw.strip():
                value = None
            elif kind.endswith("int"):
                value = int(raw)
            elif kind.endswith("float"):
                value = float(raw)
            else:
                value = str(raw)
            group = config if bank == "input" else getattr(config, bank)
            setattr(group, key, value)
        self.read_identity()
        return config
