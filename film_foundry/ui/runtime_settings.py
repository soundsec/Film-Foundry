"""Desktop settings for existing input, resource, and output capabilities."""

import copy

from film_foundry.core.frame_identity import normalize_frame_identity
from film_foundry.ui.config_fields import add_apply_button
from film_foundry.ui.i18n import tr, current_language
from film_foundry.ui.qt import ui, QtCore, QtWidgets
from film_foundry.ui.widgets import VerticalScrolledFrame
from film_foundry.ui.precision_labels import PRECISION_TOOLTIP


# Explicit public fields. Private discard policies, model topology switches and
# unimplemented film rebate geometry deliberately have no settings here.
SETTINGS = (
    ("input", "input_icc_policy", "str", ("apply", "ignore"), "输入 ICC 策略", "Input ICC policy"),
    ("look", "look_strength", "float", (), "整体微调混合强度", "Overall look adjustment strength"),
    ("processing", "comfort_zone_megapixels", "float", (), "舒适区提醒 MP（不限制尺寸）", "Comfort-zone warning MP (no size limit)"),
    ("processing", "native_thread_limit", "int", (), "原生线程上限（0=库默认）", "Native thread limit (0: library default)"),
    ("processing", "reduced_profile", "str", ("builtin_reduced_fast_v4", "builtin_reduced_fast_v3", "builtin_reduced_fast_v2", "builtin_reduced_fast_v1"), "降阶方案（仅降阶开启时生效。基础快速冲洗：取消滚动、局部接触、污染操作、乳剂剪切脱落、复杂残留水洗及同浴次反应；省略显影邻接、DIR和逐浴颗粒矩。仅保主反应、简化密度药痕、真实化学灰雾与简化颗粒。高级事故参数不生效；主图尺寸、扫描和输出文件质量保持。）", "Reduction profile (only when enabled. Basic fast: omit rotary/local contact, process incidents, shear/wear, detailed residue/wash, secondary bath reactions, adjacency, DIR and grain moments. Retain primary reactions, simple density stain, chemical fog and simplified grain. Advanced incident settings have no effect. Image size, scanner and output file quality are unchanged.)"),
    ("processing", "memory_budget_mb", "optional_float", (), "内存预算 MiB（空=不指定）", "Memory budget MiB (blank: unspecified)"),
    ("processing", "memory_budget_policy", "str", ("allow", "warn", "error"), "预算策略", "Budget policy"),
    ("processing", "history_storage_policy", "str", ("full", "cold_fp16"), "历史阶段保留", "History storage policy"),
    ("processing", "contact_precision", "str", ("auto", "float64"), "液面计算精度（随质量档：低、中使用FP32；高使用FP64。狭窄液面和极短时间窗自动保留FP64。不改变扫描或保存精度。）", "Wet-front arithmetic (quality-linked: draft/standard FP32; high FP64. Narrow fronts and short time windows retain FP64. Independent of scan and storage.)"),
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
    ("output", "material_storage_precision", "str", ("auto", "full"), "材料保存精度", "Material storage precision"),
    ("output", "medium_preview_long_edge", "int", (), "介质预览长边（0=原尺寸；仅浏览图，完整层包保留原尺寸，不影响母版和扫描）", "Medium preview long edge (0: original; browsing only, full layer packs stay native; no effect on master or scan)"),
    ("output", "encode_tile_rows", "int", (), "编码分块行数（0=不分块）", "Encoding tile rows (0: untiled)"),
    ("output", "encode_tile_threshold_megapixels", "float", (), "编码分块阈值 MP", "Encoding tiling threshold MP"),
    ("output", "scanner_raw_border_min_px", "int", (), "scanner raw 最小片基边框 px", "Minimum scanner raw base border px"),
    ("output", "watermark_metadata", "bool", (), "写入来源元数据", "Write provenance metadata"),
    ("output", "watermark_negative_material", "bool", (), "介质导出来源标记", "Medium export provenance mark"),
    ("output", "watermark_scanner_raw_border", "bool", (), "scanner raw 边框来源标记", "Scanner raw border provenance mark"),
)

CHOICE_LABELS = {
    "reduced_profile": (("基础快速冲洗", "Basic fast"), ("兼容 v3", "Legacy v3"), ("兼容 v2", "Legacy v2"), ("兼容 v1", "Legacy v1")),
    "input_icc_policy": (("转换到工作色彩空间", "Convert to working space"), ("忽略", "Ignore")),
    "memory_budget_policy": (("继续", "Continue"), ("提醒", "Warn"), ("停止", "Stop")),
    "history_storage_policy": (("完整精度", "Full precision"), ("半精度历史", "Half-precision history")),
    "contact_precision": (("随质量档", "Follow quality"), ("始终 FP64", "Always FP64")),
    "medium_npz_compression": (("压缩存储", "Compressed"), ("不压缩", "Uncompressed")),
    "material_storage_precision": (("随质量档", "Follow quality"), ("全高精度", "Full precision")),
}


class RuntimeSettings:
    def __init__(self, *, parent, config, on_apply, identity=None, embedded=False):
        self.root = ui.Window(parent,embedded=embedded)
        self.root.title(tr("button.runtime_settings"))
        self.root.geometry("760x560")
        self._base_config = copy.deepcopy(config)
        self._on_apply = on_apply
        self.status = ui.TextValue(value="")
        self.values = {}
        shell = ui.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        self.tabs = QtWidgets.QTabWidget(shell.qt)
        self.tabs.setToolTip(tr("runtime.help"))
        shell._ensure_pack_layout().addWidget(self.tabs, 1)
        pages = {}
        rows = {}
        for key, zh, en in (("input", "输入与身份", "Input & identity"),
                            ("output", "输出", "Output"),
                            ("performance", "性能", "Performance"),
                            ("advanced", "高级", "Advanced")):
            page = ui.Frame(shell, padding=8)
            scroller = VerticalScrolledFrame(page, canvas_width=640)
            scroller.pack(fill="both", expand=True)
            pages[key] = scroller.content
            pages[key].columnconfigure(1, weight=1)
            pages[key]._ensure_grid_layout().setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            rows[key] = 0
            self.tabs.addTab(page.qt, zh if current_language() == "zh_CN" else en)
        self.controls = pages["input"]
        for bank, key, kind, choices, zh, en in SETTINGS:
            section = "input" if bank in ("input", "look") else "output" if bank == "output" else "performance"
            if "_tile_" in key or "_work_long_edge" in key:
                section = "advanced"
            controls, row = pages[section], rows[section]
            group = config if bank == "input" else getattr(config, bank)
            value = getattr(group, key)
            variable = ui.BoolValue(value=value) if kind == "bool" else ui.TextValue(value="" if value is None else str(value))
            self.values[key] = variable
            label = zh if current_language() == "zh_CN" else en
            caption = label.split("（")[0].split(" (")[0]
            if key == "material_storage_precision":
                label += '\n' + PRECISION_TOOLTIP[0 if current_language() == "zh_CN" else 1]
            if key == "render_long_edge":
                caption = "处理长边" if current_language() == "zh_CN" else "Processing long edge"
            if kind == "bool":
                entry = ui.Toggle(controls, text=caption, variable=variable)
                entry.grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
            else:
                title = ui.Label(controls, text=caption, wraplength=300)
                title.qt.setMinimumWidth(210)
                title.set_tooltip(label)
                title.grid(row=row, column=0, sticky="w", pady=6, padx=(0,16))
                if choices:
                    labels = CHOICE_LABELS.get(key)
                    if labels:
                        display = tuple(pair[0 if current_language() == "zh_CN" else 1] for pair in labels)
                        entry = ui.Choice(controls, values=display, state="readonly")
                        def select(value, combo=entry.qt, options=choices):
                            blocker = QtCore.QSignalBlocker(combo)
                            combo.setCurrentIndex(options.index(str(value)) if str(value) in options else -1)
                            del blocker
                        variable._bind_view(select, entry)
                        entry.qt.currentIndexChanged.connect(lambda index, value=variable, options=choices: value.set(options[index]) if index >= 0 else None)
                    else:
                        entry = ui.Choice(controls, textvariable=variable, values=choices, state="readonly")
                else:
                    entry = ui.TextInput(controls, textvariable=variable)
                entry.grid(row=row, column=1, sticky="ew", pady=6)
            entry.set_tooltip(label)
            rows[section] += 1
        identity = identity or {}
        self.identity_values = {}
        row = rows["input"]
        for key in ("roll_id", "frame_id", "frame_index"):
            ui.Label(self.controls, text=tr("identity." + key)).grid(row=row, column=0, sticky="w", pady=3)
            value = ui.TextValue(value=str(identity.get(key, "")))
            self.identity_values[key] = value
            entry = ui.TextInput(self.controls, textvariable=value)
            entry.set_tooltip(tr("identity.help"))
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            row += 1
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
