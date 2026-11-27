# Translating Film Foundry for Android

在手机 **设置 → 外观与语言 → 导入语言包…** 选择 UTF-8 JSON，应用会安装并切换语言。也可以直接把 `<code>.json` 放进 `Android/data/com.filmfoundry.mobile/files/languages/` 后重启并选择语言。普通用户可用系统文件选择器从「下载」导入。个人导入优先于目录挂载，同一代码再次导入即可更新，单包上限 8 MiB。

## Try a translation on a phone

Run the commands below from the repository root using **Python 3.10+** (standard library only). Keep your working JSON outside Android's `assets` directories.

```sh
python android/tools/mobile_language.py template translations/fr_CA.json --code fr_CA --name "Français (Canada)"
```

Fill in as many entries under `translations` as you want to test—even two entries are enough for a first check:

```json
{
  "text:Settings": "Réglages",
  "key:mobile.scan.drawer_tool": "{tool} · Réglages du scan"
}
```

On a build with runtime language support, open **Settings → Appearance & language → Import language pack…** and select the working JSON from Downloads. Importing a replacement activates it immediately. For directory installation, copy `<code>.json` into `Android/data/com.filmfoundry.mobile/files/languages/`, restart the app and select its native name. Imported copies take precedence over directory packs, which take precedence over bundled assets.

- With **System** selected, the app checks the primary system locale and then its parent locales (for example, `fr_CA` then `fr`). Locales without a matching pack fall back to Chinese for Chinese locales and English otherwise.
- Within a selected pack, blank or missing entries fall back to its declared `fallback` (`en_US` or `zh_CN`).
- By default, `pack` writes to `android/app/src/debug/assets/languages/<code>.json` (debug builds only). To bundle a translation in a custom distribution build, pass `--out-dir android/app/src/main/assets/languages`. Stock builds ship Chinese, English, and System.

## Maintain a dictionary or reuse a desktop translation

Android working files share the desktop `film-foundry-language` v1 format (valid codes such as `fr`, `pt_BR`, `sr_Latn`; built-in `zh_CN` and `en_US` are reserved).

To start from an existing desktop translation, copy its working JSON into your translation workspace and run:

```sh
python android/tools/mobile_language.py template translations/fr_CA.json
python android/tools/mobile_language.py pack translations/fr_CA.json
```

`template` preserves existing translations and appends missing mobile entries, carrying shared English phrases over automatically; `pack` exports only translated entries recognized by the mobile catalog while keeping desktop-only and reference data intact in your working file.

## Guidelines for developers porting a screen

- **Static UI pairs**: `shell.copy(zh, en)` looks up `text:<en>` verbatim (preserve spaces and punctuation). Each catalog entry includes its source filename for translator context.
- **Dynamic messages**: Use a stable `mobile.`-prefixed key via `shell.words.message("mobile.scan.drawer_tool", "扫描调整 · {tool}", "Scan controls · {tool}", "tool" to label)`. Translators may reorder named placeholders but must preserve every placeholder name and count (verified by `pack`).
- **Keep logic language-independent**: Pass IDs, filenames, recipe keys, and user-entered names as data; translate only the surrounding sentence.
- **Current migration notes**: Some older interpolated status strings, diagrams, native camera summaries, and technical presenters still branch directly on built-in languages and are being migrated incrementally. System dialogs follow Android's own locale. Check longer strings and RTL layouts on a real device before distributing a port.
