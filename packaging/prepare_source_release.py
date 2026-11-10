"""Prepare allowlisted desktop/optional Android sources or public documents.

This never updates a Git checkout, stages files, deletes files or copies user
images/settings. Review the prepared tree before merging it into a checkout.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil


PUBLIC_DOCS = (
    "README.md", "DESKTOP_GUIDE.md", "REFERENCE.md", "SHOWCASE.md", "CHANGELOG.md",
    "MOBILE_GUIDE.md", "MOBILE_GUIDE.en.md", "MOBILE_SHOWCASE.md", "MOBILE_SHOWCASE.en.md",
    "DEVELOPMENT_SNAPSHOT.md",
)
ROOT_FILES = (
    ".gitignore", ".gitattributes", "README.md", "README.en.md", "LICENSE", "COPYING",
    "THIRD_PARTY_NOTICES.md", "pyproject.toml", "requirements.txt",
    "environment.yml", "run_film_foundry_launcher.py", "run_material_windows.py",
)
PUBLIC_BUILD_FILES = (
    "README.md", "build_windows_exe.ps1", "FilmFoundry.spec",
    "collect_windows_licenses.ps1", "collect_native_notices.py",
    "PORTABLE_README.txt", "prepare_source_release.py",
    "requirements-build.txt", "environment-build.yml",
)


def copy_source_file(source: Path, target: Path) -> None:
    if source.suffix.lower() in {".png",".jpg",".webp",".ico",".npz",".bin",".jar",".dng"}:
        shutil.copy2(source,target)
    else:
        # Stable text diffs across Windows and Unix; no code reformatting.
        target.write_text(source.read_text(encoding="utf-8-sig"),encoding="utf-8",newline="\n")


def public_links(content: str) -> str:
    content=content.replace("docs/public/","docs/")
    content=content.replace("(../images/","(images/")
    content=content.replace('src="../images/', 'src="images/')
    content=content.replace("(../../THIRD_PARTY_NOTICES.md)","(../THIRD_PARTY_NOTICES.md)")
    content=content.replace("(../../third_party_licenses/", "(../third_party_licenses/")
    content=content.replace("(../../android/", "(../android/")
    content=content.replace("(../../packaging/", "(../packaging/")
    for name in PUBLIC_DOCS:
        if name == "README.md":
            continue  # ../README.md is the project homepage, not the docs index.
        content=content.replace(f"(../{name})",f"({name})")
    for name in ("README.md", "README.en.md"):
        content=content.replace(f"(../../{name})",f"(../{name})")
    return content


def copy_public_docs(source: Path, target: Path, *, include_root: bool = True) -> int:
    destination=target/"docs";destination.mkdir(parents=True,exist_ok=True)
    count=0
    for name in PUBLIC_DOCS:
        candidates=(source/"docs"/"public"/name,source/"docs"/name)
        path=next((path for path in candidates if path.is_file()),None)
        if path is not None:
            content=public_links(path.read_text(encoding="utf-8-sig"))
            if not include_root:
                content=content.replace("(../README.md)", "(../README.txt)").replace("(../README.en.md)", "(../README.txt)")
                content=content.replace("(../THIRD_PARTY_NOTICES.md)", "(../licenses/THIRD_PARTY_NOTICES.md)")
                for folder in ("third_party_licenses", "packaging", "android"):
                    content=content.replace(f"(../{folder}/", f"(https://github.com/soundsec/Film-Foundry/blob/main/{folder}/")
            (destination/name).write_text(content,encoding="utf-8",newline="\n")
            count+=1
    for name in (("README.md","README.en.md","THIRD_PARTY_NOTICES.md") if include_root else ()):
        path=source/name
        if path.is_file():
            (target/name).write_text(public_links(path.read_text(encoding="utf-8-sig")),encoding="utf-8",newline="\n")
    for image_folder in ("desktop-0.4.0", "mobile-0.1.0", "desktop-dev-2026-11-10"):
        image_source=source/"docs"/"images"/image_folder
        if not image_source.is_dir():
            continue
        image_target=destination/"images"/image_folder
        image_target.mkdir(parents=True,exist_ok=True)
        for image in sorted(path for path in image_source.iterdir() if path.suffix.lower() in {".png", ".jpg", ".svg"} and path.is_file()):
            shutil.copy2(image,image_target/image.name);count+=1
    # Project overview pictures have stable names independent of a release.
    for name in ("showcase.jpg", "shared-process.jpg", "material-details.jpg"):
        image=source/"docs"/"images"/name
        if image.is_file():
            (destination/"images").mkdir(parents=True,exist_ok=True)
            shutil.copy2(image,destination/"images"/name);count+=1
    return count


def copy_android_sources(source: Path, target: Path) -> int:
    android = source / "android"
    files = [android / name for name in (
        "README.md", "build.gradle.kts", "settings.gradle.kts", "gradle.properties",
        "gradlew.bat", "gradle/libs.versions.toml", "gradle/wrapper/gradle-wrapper.jar",
        "gradle/wrapper/gradle-wrapper.properties",
    )]
    suffixes = {".kt", ".java", ".xml", ".json", ".png", ".webp", ".npz", ".dng", ".txt", ".bin", ".ffc2", ".py"}
    for module in ("app", "platform", "workflow", "kernel", "interchange"):
        files.append(android / module / "build.gradle.kts")
        files.extend(path for path in (android / module / "src").rglob("*")
                     if path.is_file() and path.suffix in suffixes
                     and not any(part.startswith(".") or part == "__pycache__"
                                 for part in path.relative_to(android).parts))
    # Reproducible presets and isolated tests, not local one-off review probes.
    files.extend(android / "tools" / name for name in (
        "sync_phone_presets.py", "sync_creative_presets.py",
        "sync_seasonal_collection.py", "run_device_tests.py", "collect_runtime_notices.py",
    ))
    for path in sorted(files):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_source_file(path, destination)
        if path.name == "README.md":
            destination.write_text(public_links(path.read_text(encoding="utf-8-sig")),
                                   encoding="utf-8", newline="\n")
    return len(files)


def prepare(source: Path, target: Path, *, include_packaging: bool = True,
            include_android: bool = False) -> int:
    # Requiring a new directory makes accidental checkout or user-data
    # overwrites impossible. No recursive deletion or mirror operation.
    if source==target or source in target.parents and target.name in {"film_foundry","android"}:
        raise ValueError("Choose a new staging directory, not a source package")
    target.mkdir(parents=True,exist_ok=False)
    copied=0
    for name in ROOT_FILES:
        path=source/name
        if not path.is_file():raise FileNotFoundError(path)
        copy_source_file(path,target/name);copied+=1
    source_folders = [
        ("film_foundry",{".py",".json",".svg",".png",".ico",".npz",".txt",".md"}),
        ("third_party_licenses",{".txt",".md",".json",".pom"}),
    ]
    for folder,suffixes in source_folders:
        for path in sorted((source/folder).rglob("*")):
            if not path.is_file():continue
            if path.suffix not in suffixes and not (
                folder == "third_party_licenses" and path.name in
                {"LICENSE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT", "METADATA"}
            ):continue
            relative=path.relative_to(source)
            if any(part.startswith(".") or part.lower().endswith('.ffworkspace') or part in {"__pycache__","build","dist"} for part in relative.parts):continue
            destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            copy_source_file(path,destination);copied+=1
    if include_packaging:
        (target/"packaging").mkdir(parents=True, exist_ok=True)
        for name in PUBLIC_BUILD_FILES:
            copy_source_file(source/"packaging"/name, target/"packaging"/name)
            if name.endswith('.md'):
                path=target/"packaging"/name
                path.write_text(public_links(path.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
            copied += 1
    copied+=copy_public_docs(source,target)
    if include_android:
        copied += copy_android_sources(source, target)
    resource=source/"film_foundry/assets/finder_resources.bin"
    if resource.is_file():
        copy_source_file(resource,target/"film_foundry/assets/finder_resources.bin");copied+=1
    return copied


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination",type=Path)
    docs_mode=parser.add_mutually_exclusive_group()
    docs_mode.add_argument("--docs-only",action="store_true")
    docs_mode.add_argument("--portable-docs",action="store_true",help="Copy public docs/images without replacing the portable README.")
    build_mode=parser.add_mutually_exclusive_group()
    build_mode.add_argument("--include-packaging",action="store_true",default=True,help="Include the small public build-file allowlist (default).")
    build_mode.add_argument("--exclude-packaging",action="store_false",dest="include_packaging",help="Omit public build files from a special-purpose staging copy.")
    parser.add_argument("--include-android",action="store_true",help="Include Android source, wrapper, synthetic fixtures and test tools; exclude local SDK paths, keys and build artifacts.")
    args=parser.parse_args()
    source=Path(__file__).resolve().parents[1];target=args.destination.resolve()
    if args.docs_only or args.portable_docs:
        if not target.is_dir():parser.error("The existing binary/source root is required")
        count=copy_public_docs(source,target,include_root=not args.portable_docs)
    else:
        count=prepare(source,target,include_packaging=args.include_packaging,
                      include_android=args.include_android)
    print(f"Prepared {count} files in {target}")


if __name__=="__main__":main()
