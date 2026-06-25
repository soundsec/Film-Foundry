"""Small UI language layer.

The project is still mostly script-first, so this intentionally stays light:
call tr("some.key") from GUI code.  The active language can be selected with
FILM_FOUNDRY_LANG=zh_CN or FILM_FOUNDRY_LANG=en_US.
"""

from __future__ import annotations

import os


DEFAULT_LANGUAGE = "zh_CN"


_MESSAGES: dict[str, dict[str, str]] = {
    "zh_CN": {
        "app.title": "Film Foundry / Electronic Negative Factory",
        "status.ready": "选择阶段模式，然后点击预览或开始处理。",
        "status.develop": "只冲洗模式：输入图片会被保存为 .npz 底片母版，不生成最终正像。",
        "status.scan": "只扫描模式：只读取已有底片，不重新运行胶片/冲洗阶段。",
        "status.full": "完整流程：输入图片会先冲洗成电子负片，再扫描成最终正像。",
        "preview.input": "输入",
        "preview.result": "结果",
        "section.stage": "阶段模式",
        "section.paths": "路径",
        "section.common": "通用",
        "section.develop": "冲洗 / 底片形成参数",
        "section.scan": "扫描 / 正像解释参数",
        "mode.full": "完整流程",
        "mode.develop": "只冲洗底片",
        "mode.scan": "只扫描底片",
        "button.preview": "预览当前阶段",
        "button.process": "开始处理",
        "button.file": "选文件",
        "button.folder": "选文件夹",
        "button.refresh_presets": "刷新预设列表",
        "button.film_editor": "材料编辑器",
        "button.develop_editor": "流程编辑器",
        "button.scanner_editor": "扫描编辑器",
        "label.input_image": "输入图片",
        "label.negative_npz": "底片 .npz",
        "label.output": "输出",
        "label.film_preset": "胶片材料预设",
        "label.develop_preset": "冲洗流程预设",
        "label.scanner_preset": "扫描 / 输出预设",
        "label.preview_output": "按预览长边输出/冲洗",
        "label.preview_long_edge": "预览长边 px（0=原图）",
        "label.fast_mode": "快速模式",
        "label.quality": "处理质量",
        "label.sidecar": "保存 sidecar JSON",
        "label.debug": "debug 中间结果",
        "label.grid": "四宫格对比",
        "label.expert": "专家模式",
        "label.output_format": "输出格式",
    },
    "en_US": {
        "app.title": "Film Foundry / Electronic Negative Factory",
        "status.ready": "Choose a stage mode, then preview or start processing.",
        "status.develop": "Develop only: input images are saved as reusable .npz negative masters.",
        "status.scan": "Scan only: read existing negatives without rerunning film/develop stages.",
        "status.full": "Full workflow: develop an electronic negative, then scan it to a final positive.",
        "preview.input": "Input",
        "preview.result": "Result",
        "section.stage": "Stage Mode",
        "section.paths": "Paths",
        "section.common": "Common",
        "section.develop": "Develop / Negative Formation",
        "section.scan": "Scan / Positive Rendering",
        "mode.full": "Full workflow",
        "mode.develop": "Develop negative only",
        "mode.scan": "Scan negative only",
        "button.preview": "Preview Current Stage",
        "button.process": "Start Processing",
        "button.file": "Choose File",
        "button.folder": "Choose Folder",
        "button.refresh_presets": "Refresh Presets",
        "button.film_editor": "Material Editor",
        "button.develop_editor": "Process Editor",
        "button.scanner_editor": "Scanner Editor",
        "label.input_image": "Input Image",
        "label.negative_npz": "Negative .npz",
        "label.output": "Output",
        "label.film_preset": "Film Material Preset",
        "label.develop_preset": "Develop Process Preset",
        "label.scanner_preset": "Scanner / Output Preset",
        "label.preview_output": "Use preview long edge",
        "label.preview_long_edge": "Preview long edge px (0=original)",
        "label.fast_mode": "Fast mode",
        "label.quality": "Quality",
        "label.sidecar": "Save sidecar JSON",
        "label.debug": "Debug intermediates",
        "label.grid": "Comparison grid",
        "label.expert": "Expert mode",
        "label.output_format": "Output format",
    },
}


def current_language() -> str:
    value = os.environ.get("FILM_FOUNDRY_LANG", DEFAULT_LANGUAGE).strip()
    return value if value in _MESSAGES else DEFAULT_LANGUAGE


def tr(key: str, *, language: str | None = None) -> str:
    lang = language or current_language()
    return _MESSAGES.get(lang, {}).get(key) or _MESSAGES[DEFAULT_LANGUAGE].get(key) or key
