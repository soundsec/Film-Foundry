"""Prepare optional Android dictionaries. Run from any directory with Python 3.10+."""
import argparse
import json
from pathlib import Path
import re
import string
import tempfile

ANDROID = Path(__file__).resolve().parents[1]
SOURCE = ANDROID / "app/src/main/kotlin/com/filmfoundry/app"
DEBUG_PACKS = ANDROID / "app/src/debug/assets/languages"
CODE = re.compile(r"[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|[0-9]{3}))?")
LITERAL = r'"(?:[^"\\\n]|\\.)*"'
TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|""".*?"""|' + LITERAL, re.S)
HAN = re.compile(r"[\u3400-\u9fff]")
KEY_CALL = re.compile(r'\bmessage\s*\(\s*"([a-z][a-z0-9_.]*)"\s*,\s*$')


def catalog():
    result = {}
    files = [*sorted((SOURCE / "ui").glob("*.kt")), SOURCE / "ProcessHostActivity.kt",
             SOURCE / "session/ProcessingService.kt"]
    for path in files:
        source = path.read_text(encoding="utf-8-sig")
        tokens = list(TOKEN.finditer(source))
        for left, right in zip(tokens, tokens[1:]):
            if source[left.end():right.start()].strip() not in {",", "to"}:
                continue
            if not HAN.search(left[0]) or HAN.search(right[0]) or "$" in left[0] + right[0]:
                continue
            try:
                zh, en = json.loads(left[0]), json.loads(right[0])
            except ValueError:
                continue
            if not en.strip() or not re.search(r"[a-zA-Z]", en):
                continue
            named = KEY_CALL.search(source[max(0, left.start()-200):left.start()])
            key = "key:" + named[1] if named else "text:" + en
            result.setdefault(key, {"zh": zh, "en": en, "source": path.name})
    return dict(sorted(result.items()))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        with temporary:
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        Path(temporary.name).replace(path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def fields(text):
    return sorted((name, spec, conversion or "") for _, name, spec, conversion
                  in string.Formatter().parse(text) if name is not None)


def compact(pack, reference):
    if not isinstance(pack, dict) or pack.get("format") != "film-foundry-language" or pack.get("version") != 1:
        raise ValueError("Expected a Film Foundry v1 language dictionary")
    code = pack.get("code")
    if not isinstance(code, str) or not CODE.fullmatch(code) or code in {"en", "zh", "en_US", "zh_CN"}:
        raise ValueError("Use a new language code such as fr, pt_BR or sr_Latn")
    if not isinstance(pack.get("name"), str) or not pack["name"].strip():
        raise ValueError("Set name to the language's display name")
    if pack.get("fallback") not in {"en_US", "zh_CN"} or not isinstance(pack.get("translations"), dict):
        raise ValueError("Expected fallback en_US or zh_CN and a translations object")
    result = {k: pack[k] for k in ("format", "version", "code", "name", "fallback")}
    translations = {}
    for key, value in pack["translations"].items():
        if not key.startswith(("key:", "text:")) or not isinstance(value, str):
            raise ValueError(f"Invalid translation entry: {key}")
        if not value.strip():
            continue
        # Desktop-only entries remain in the author's dictionary. The APK needs mobile entries.
        if key not in reference:
            continue
        if fields(value) != fields(reference[key]["en"]):
            raise ValueError(f"Preserve the placeholders in {key}")
        translations[key] = value
    result["translations"] = translations
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="create or update a translator's working file")
    template.add_argument("file", type=Path)
    template.add_argument("--code")
    template.add_argument("--name")
    template.add_argument("--fallback", choices=["en_US", "zh_CN"])
    pack = commands.add_parser("pack", help="validate and write a compact Android asset")
    pack.add_argument("file", type=Path)
    pack.add_argument("--out-dir", type=Path, default=DEBUG_PACKS)
    args = parser.parse_args()
    ref = catalog()
    if args.command == "template":
        value = read(args.file) if args.file.exists() else {
            "format": "film-foundry-language", "version": 1, "fallback": "en_US", "translations": {}}
        for key in ("code", "name", "fallback"):
            if getattr(args, key) is not None:
                value[key] = getattr(args, key)
        # An old translation may need new fields. Refresh its reference before asking for repairs.
        compact(value, {})
        for key in ref:
            value["translations"].setdefault(key, "")
        value["reference"] = {**value.get("reference", {}), **ref}
        write(args.file, value)
        print(f"{len(ref)} mobile entries; working file: {args.file}")
    else:
        value = compact(read(args.file), ref)
        destination = args.out_dir / (value["code"] + ".json")
        if destination.resolve() == args.file.resolve():
            raise ValueError("Keep the working file separate from the packaged asset")
        write(destination, value)
        print(f"{len(value['translations'])}/{len(ref)} mobile entries translated: {destination}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
