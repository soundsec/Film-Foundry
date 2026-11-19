# Translating Film Foundry for Android

**中文速览**　在仓库根目录用下面的 `template` 命令创建译者工作文件。按需填写 `translations`，每项对应的中英文和源文件在 `reference`。运行 `pack`，编译调试 APK，在「设置 → 外观与语言」选择译本。未译条目沿用英语，可将 `fallback` 设为 `zh_CN` 以沿用中文。更新源码后，对同一个工作文件再运行 `template` 即可补入新词条，已有译文会保留。

## Try a translation on a phone

Run the following from the repository root with Python 3.10 or later. The tool uses the standard library. Keep the working JSON outside Android's asset directories.

```sh
python android/tools/mobile_language.py template translations/fr_CA.json --code fr_CA --name "Français (Canada)"
```

Fill in as many entries as you like. For example, a translator might start with these two entries in `translations`.

```json
{
  "text:Settings": "Réglages",
  "key:mobile.scan.drawer_tool": "{tool} · Réglages du scan"
}
```

Package the translation and build the debug APK with JDK 17.

```sh
python android/tools/mobile_language.py pack translations/fr_CA.json
cd android
.\gradlew.bat --offline :app:assembleDebug
```

Install the APK and choose the language by its own name under **Settings → Appearance & language**. With **System** selected, the app tries the primary system locale, then its parents (for example, `fr_CA` followed by `fr`). For a locale without a pack, Chinese locales use Chinese and other locales use English. Within a selected pack, blank or absent entries use its declared fallback. Switch back to a built-in language from the same screen.

The default pack destination is `android/app/src/debug/assets/languages/<code>.json`. It is included only in debug builds. A distributor who wishes to ship a translation can explicitly pass `--out-dir android/app/src/main/assets/languages` to `pack` before building their distribution. Language files are bundled at build time. Stock installations provide Chinese, English and the system setting.

## Maintain a dictionary or reuse a desktop translation

The working file uses the desktop `film-foundry-language` version 1 fields. Use a language code such as `fr`, `pt_BR` or `sr_Latn` and a native display name. The two built-in languages have reserved codes. The supported fallbacks are `en_US` and `zh_CN`.

To start from a desktop dictionary, copy the **working** JSON file to your translation workspace. Run the following after a source update or to add the mobile reference to the copy.

```sh
python android/tools/mobile_language.py template translations/fr_CA.json
python android/tools/mobile_language.py pack translations/fr_CA.json
```

The first command keeps existing translations and adds missing entries. The second writes only translated entries found in the current mobile catalog. Shared English phrases carry over automatically. The working file retains desktop-only entries, old entries and translator reference data. Keep it for future updates.

## Add copy when porting a screen

- Existing paired UI labels use `shell.copy(zh, en)`. A static pair such as `shell.copy("设置", "Settings")` looks up `text:Settings`. Keep spaces and punctuation in the key. The catalog includes literal pairs in UI source and recognized presenter entry points. The source filename next to each entry helps a translator find its context.
- For new dynamic wording or a phrase that needs its own context, use a stable, mobile-prefixed key. For example, `shell.words.message("mobile.scan.drawer_tool", "扫描调整 · {tool}", "Scan controls · {tool}", "tool" to label)`. Translators can reorder named fields. Preserve every field and its occurrence count. The packaging command checks them before writing the asset.
- Pass original object identifiers, filenames, recipe keys and user-entered names as data. Translate the sentence that displays them. Keep application decisions independent of translated text.
- Some older status lines interpolate values before reaching the copy helper. Diagrams, native camera descriptions and several technical presenters also have built-in-language branches. Those surfaces need a keyed message or an explicit UI text lookup as they are migrated. Android supplies its own system-dialog text. Check longer labels and right-to-left layouts on the device when developing a port.
