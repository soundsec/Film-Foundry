# Translating Film Foundry Desktop

[中文](TRANSLATING.md) · [Desktop guide](DESKTOP_GUIDE.en.md) · [Project home](../README.en.md)

Place a UTF-8 dictionary such as `fr_FR.json` in the **languages folder beside FilmFoundry.exe** (the project root for source runs). Reopen **Settings → Appearance & Layout** and select its native name. Settings can open the folder, export a template, or import a personal copy. Personal copies take precedence over shared dictionaries with the same code. Reopen Settings to discover added or removed files. Restart the workbench after editing the active translation.

## Creating and importing a language pack

1. Set `code` to your target language code (for example `fr_FR`, `ja_JP`, or `zh_TW`) and `name` to its native display name. Built-in codes `zh_CN` and `en_US` cannot be overwritten.
2. Set `fallback` to either `en_US` or `zh_CN`; any blank or omitted entries fall back to that language.
3. Edit only the values inside `translations`. The `reference` block provides Chinese/English context and is ignored at runtime. You can translate a subset first and leave remaining entries empty or remove them.
4. Preserve every formatting placeholder and specification (such as `{count}` or `{size:.1f}`). You may reorder placeholders, but do not rename, remove, or alter their format specifiers; invalid packs are rejected while keeping the previous pack intact.
5. Select the imported language from the list. The main workbench rebuilds immediately while preserving current work parameters; already-open material windows, editors, and Settings update after reopening. Restart the app after updating the currently active language pack.

### Minimal example

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

## Entry rules and incremental updates

- `key:` maps to stable string identifiers and `text:` maps to existing English UI labels (identical English strings share one translation). Never translate keys, parameter field names, preset IDs, or file paths; user-created material names stay as entered.
- **Incremental updates**: Select an installed language and export a template again to merge existing translations with newly added strings (new items remain blank).
- **Storage & safety**: Imported packs are copied to `desktop_ui_languages/<code>.json` next to UI preferences (8 MiB limit). Packs are parsed strictly as data—no code execution or network access—and stay separate from recipes, images, media, and crash recovery.
- **Coverage**: Desktop and Android read the same dictionary format. Each uses the entries referenced by its own interface. Older composed messages, low-level errors and native dialogs may use their own language sources. [Installing packs on Android](../android/TRANSLATING.md)

## For developers

Use `tr("stable.key")` or `text("中文", "English")` for new UI copy. When formatting dynamic values, translate the template before formatting (for example `text("{count} 个预设", "{count} presets").format(count=n)`) instead of passing evaluated f-strings. After changing static copy, regenerate the reference catalog:

```bash
python -m film_foundry.tools.build_translation_catalog
```

The generated `ui/translation_catalog.py` is used only when exporting templates; runtime never scans source files. Changing an English `text:` string requires retranslation for that entry. If material reference algorithms, targets, or thumbnail decoding semantics change, bump `selection-v1` / `library-v1` cache versions accordingly.
