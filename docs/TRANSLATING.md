# 为桌面版添加语言

[English](TRANSLATING.en.md) · [桌面使用指南](DESKTOP_GUIDE.md) · [项目首页](../README.md)

将 UTF-8 翻译包（例如 `fr_FR.json`）放到 **FilmFoundry.exe 同级的 languages 文件夹**，重新打开 **全局设置 → 外观与布局**，选择包内的语言名称。源码运行时使用项目根目录的 `languages/`。在设置里可以打开该目录、导出模板，或直接导入 JSON 安装个人副本。个人副本优先于同代码的共享语言包。移除一份后重新打开设置可重新发现目录，更新正在使用的译文后重启工作台。

## 制作与导入步骤

1. 将 `code` 改为目标语言代码（如 `fr_FR`、`ja_JP`、`zh_TW`），将 `name` 改为该语言的本地名称（内置 `zh_CN` 与 `en_US` 不可覆盖）。
2. 将 `fallback` 设为 `en_US` 或 `zh_CN`，未填写译文的条目将自动回退到该语言。
3. 只需编辑 `translations` 中的值（`reference` 仅供中英对照，不参与界面显示）。可先翻译部分词条，其余留空或删除。
4. 保留所有格式化占位符（如 `{count}`、`{size:.1f}`）：可以调整语序，但不可改名、删减或修改格式；导入校验失败会报错并保留原有语言包。
5. 导入后在列表中选择新语言。主工作台会保留当前工作参数并即时重建；已打开的材料页、编辑器与设置窗口重开后生效。更新正在使用的语言包后请重启程序。

### 最小示例

```json
{
  "format": "film-foundry-language",
  "version": 1,
  "code": "fr_FR",
  "name": "Français",
  "fallback": "en_US",
  "translations": {
    "key:theme.dark": "Sombre",
    "text:Choose material": "Choisir un matériau",
    "text:{count} presets": "{count} préréglages"
  }
}
```

## 词条规则与增量更新

- `key:` 对应稳定词条标识，`text:` 对应界面英文原文，相同英文原文共用译文。请勿翻译键名、参数字段名、预设标识或文件路径；用户自定义材料名保持原样。
- **增量更新**：选中已安装语言再导出模板，会自动保留已有译文并把新版新增词条留空，便于迭代维护。
- **存储与安全**：导入后语言包复制到偏好目录旁的 `desktop_ui_languages/<语言代码>.json`（上限 8 MiB），纯 JSON 数据读取，不执行代码、不联网，与配方、图像和自动保险完全隔离。
- **当前覆盖范围**：桌面与 Android 共用语言包格式，各自从包中读取本端使用的词条。历史动态拼接文本、底层异常及系统原生对话框仍有各自的语言来源。[手机安装方法](../android/TRANSLATING.md)

## 给开发者

新增界面文案请统一使用 `tr("稳定键")` 或 `text("中文", "English")`。带变量时先翻译模板再格式化（如 `text("{count} 个预设", "{count} presets").format(count=n)`），避免把 f-string 运行结果当成词条键。更新静态文本后运行以下命令刷新参考目录：

```bash
python -m film_foundry.tools.build_translation_catalog
```

生成的 `ui/translation_catalog.py` 仅用于导出翻译模板，运行时不扫描源码。修改英文原文的 `text:` 项需要译者重新翻译；若更新了材料参考算法、标靶或缩略图解码语义，请同步递增 `selection-v1` / `library-v1` 缓存版本号。
