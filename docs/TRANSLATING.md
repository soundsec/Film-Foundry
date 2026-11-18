# 为桌面版添加语言

无需修改 Python 或重新打包。在 **全局设置 → 外观与布局** 导出翻译模板，用支持 UTF-8 的文本编辑器编辑 JSON，再导入语言包并在语言列表中选择它。

1. 将 `code` 改为新语言代码，例如 `fr_FR`、`ja_JP` 或 `zh_TW`，将 `name` 改为语言的本地名称。内置 `zh_CN`、`en_US` 不可被替换。
2. `fallback` 选择 `en_US` 或 `zh_CN`；未填写的译文使用该语言。
3. 只修改 `translations` 中的值。`reference` 提供中英对照，不参与界面显示。可以先翻译一部分，其他值留空；也可删除尚未翻译的条目。
4. 保留所有占位符及格式，例如 `{count}`、`{size:.1f}`。可以调整位置，不能改名、删除或改变格式。导入时不合规会报错，并保留原有语言包。
5. 导入后选择新语言。主工作台沿用保留当前工作参数的重建流程；已有材料页、编辑器和设置窗口需重开。更新正在使用的语言包后请重新启动程序。

最小示例：

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

`key:` 对应稳定词条标识，`text:` 对应界面现有英文原文，两者都由同一个语言包处理。相同英文文本共用译文。不要翻译这些键、参数字段名、预设标识或文件路径；自定义材料名称保留用户输入。

选择已安装语言后再导出模板，会合并现有译文与新版程序的词条，新增项留空，便于增量翻译。导入会复制语言包到 UI 偏好旁的 `desktop_ui_languages/语言代码.json`（自定义偏好路径时随文件名变化），与配方、图像、介质和自动保险分离。只读取 JSON 数据，不执行代码，也不需要网络。大小上限 8 MiB。

目前覆盖内置词条表、主工作台／材料窗口／编辑器等静态双语入口。部分旧的动态拼接消息、底层异常、尚未接入的品牌预设名称和系统文件对话框不会全部随包翻译。安卓使用自身的资源系统，本格式当前仅供桌面版使用。此模板不是“整套界面已完成第三语言翻译”的承诺。

## 给开发者

新文本统一使用 `tr('稳定键')` 或 `text('中文', 'English')`。带变量时先翻译模板再格式化，例如 `text('{count} 个预设', '{count} presets').format(count=n)`，避免把 f-string 的运行结果当作词条键。改变静态文本后运行：

```console
python -m film_foundry.tools.build_translation_catalog
```

生成的 `ui/translation_catalog.py` 只用于导出参考模板；运行时不扫描源码。语言包旧键仍保留，但改过英文原文的 `text:` 项需要重新翻译。涉及新的材料参考算法、标靶或缩略解码语义时，同步递增 `selection-v1`／`library-v1` 缓存版本；普通配方与来源变化已由实际参数及文件修订自动失效。
