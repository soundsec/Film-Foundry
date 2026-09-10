import re
import subprocess
import sys
from pathlib import Path

import film_foundry
import half_frame_darkroom

from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_cli_module_entrypoint_matches_canonical():
    for arguments, expected in [(["--help"], 0), (["invalid-command"], 2)]:
        results = [subprocess.run(
            [sys.executable, "-m", name, *arguments], cwd=PROJECT_ROOT,
            capture_output=True, text=True,
        ) for name in ("film_foundry.app.cli", "half_frame_darkroom.app.cli")]
        assert [result.returncode for result in results] == [expected, expected]
        assert results[0].stdout == results[1].stdout
        assert results[0].stderr == results[1].stderr


def test_release_version_and_license_metadata_are_consistent():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
    assert version_match is not None
    assert version_match.group(1) == half_frame_darkroom.__version__ == "0.3.1"
    assert film_foundry.__version__ == half_frame_darkroom.__version__
    assert 'license = "GPL-3.0-or-later"' in pyproject

    notice = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    copying = (PROJECT_ROOT / "COPYING").read_text(encoding="utf-8")
    assert "SPDX-License-Identifier: GPL-3.0-or-later" in notice
    assert "GNU GENERAL PUBLIC LICENSE" in copying
    assert "Version 3, 29 June 2007" in copying


def test_release_sources_include_presets_and_reproducible_windows_build_files():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.setuptools.package-data]' in pyproject
    assert 'presets/*/*.json' in pyproject
    for preset_kind in ("film", "develop", "scanner"):
        assert list(
            (PROJECT_ROOT / "film_foundry" / "presets" / preset_kind).glob("*.json")
        )

    assert (PROJECT_ROOT / "packaging" / "FilmFoundry.spec").is_file()
    assert (PROJECT_ROOT / "packaging" / "build_windows_exe.ps1").is_file()
    assert (PROJECT_ROOT / "packaging" / "PORTABLE_README.txt").is_file()
    build_script = (PROJECT_ROOT / "packaging" / "build_windows_exe.ps1").read_text(
        encoding="utf-8"
    )
    assert 'docs\\public\\*.md' in build_script
    assert 'docs\\internal' not in build_script
    assert 'Join-Path $ProjectRoot "README.md"' in build_script
    assert "README.zh-CN.md" not in build_script


def test_source_run_keeps_writable_and_resource_roots_explicit():
    from film_foundry.app.paths import app_root as core_app_root
    from film_foundry.app.paths import resource_root as core_resource_root

    assert app_root is core_app_root
    assert resource_root is core_resource_root
    assert app_root() == PROJECT_ROOT
    assert resource_root() == PROJECT_ROOT
    assert (resource_root() / "film_foundry" / "presets").is_dir()


def test_frozen_and_bundle_path_helpers_use_executable_and_meipass(monkeypatch, tmp_path):
    import film_foundry.app.paths as app_paths

    exe = tmp_path / "dist" / "FilmFoundry.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    bundle = tmp_path / "_internal"
    bundle.mkdir()
    monkeypatch.setattr(app_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_paths.sys, "executable", str(exe))
    monkeypatch.setattr(app_paths.sys, "_MEIPASS", str(bundle), raising=False)
    assert app_paths.app_root() == exe.parent.resolve()
    assert app_paths.resource_root() == bundle.resolve()



def test_launcher_release_self_check_covers_bundled_tools_and_presets():
    from run_film_foundry_launcher import release_self_check

    release_self_check()


def test_windows_builder_is_qt_only_and_verifies_platform_plugin():
    build_script = (PROJECT_ROOT / "packaging" / "build_windows_exe.ps1").read_text(
        encoding="utf-8"
    )
    assert '"--self-check"' in build_script
    assert "TclPatchLevel" not in build_script
    assert "TCL_LIBRARY" not in build_script

    spec = (PROJECT_ROOT / "packaging" / "FilmFoundry.spec").read_text(
        encoding="utf-8"
    )
    assert 'excludes=["tkinter", "_tkinter"]' in spec
    assert "tcl86t.dll" not in spec
    assert "tk86t.dll" not in spec
    assert "SkipFrozenSelfCheck" in build_script
    assert "from PySide6 import QtCore" in build_script
    assert 'Filter "qwindows.dll"' in build_script


def test_desktop_gui_has_no_tk_backend_or_runtime_imports():
    runtime_files = [
        PROJECT_ROOT / "run_film_foundry_launcher.py",
        *(PROJECT_ROOT / "film_foundry" / "ui").glob("*.py"),
        *(PROJECT_ROOT / "film_foundry" / "tools").glob("run_*editor.py"),
        PROJECT_ROOT / "film_foundry" / "tools" / "run_darkroom_gui.py",
        PROJECT_ROOT / "film_foundry" / "tools" / "run_slide_light_table_tester.py",
    ]
    runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
    assert "import tkinter" not in runtime_text
    assert "from tkinter" not in runtime_text
    assert "FILM_FOUNDRY_UI_BACKEND" not in runtime_text
    assert not (PROJECT_ROOT / "film_foundry" / "ui" / "backend.py").exists()


def test_desktop_dependencies_include_qt_runtime():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    environment = (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")

    for content in (requirements, pyproject, environment):
        assert "PySide6-Essentials>=6.8,<7" in content
        assert "python-lcms2>=0.1.1,<0.2" in content


def test_public_and_internal_documents_are_physically_separated():
    public_docs = PROJECT_ROOT / "docs" / "public"
    internal_docs = PROJECT_ROOT / "docs" / "internal"
    assert public_docs.is_dir()
    assert internal_docs.is_dir()

    assert (public_docs / "RELEASE_NOTES_0.3.md").is_file()
    assert (public_docs / "PRESET_GUIDE.md").is_file()
    assert (internal_docs / "STRUCTURE.md").is_file()
    assert (internal_docs / "EXE_PACKAGING.md").is_file()
    assert (internal_docs / "RELEASE_CHECKLIST.md").is_file()

    forbidden_public_references = (
        "docs/internal",
        "EXE_PACKAGING.md",
        "RELEASE_CHECKLIST.md",
        "EVIDENCE_AND_VALIDATION.md",
        "PERFORMANCE_STRATEGY.md",
        "STRUCTURE.md",
    )
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in public_docs.glob("*.md")
    )
    assert not any(token in public_text for token in forbidden_public_references)


def test_homepage_is_chinese_first_and_does_not_link_internal_docs():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Film Foundry\n")
    assert "## 主要功能" in readme
    assert "[English README](README.en.md)" in readme
    assert "EXE_PACKAGING" not in readme
    assert "docs/internal" not in readme


def test_written_project_identity_stays_on_historical_string():
    from film_foundry.core import provenance, sidecar
    from film_foundry.identity import (
        APP_DISPLAY_NAME,
        KNOWN_PROJECT_NAMES,
        PROJECT_NAME,
    )
    from film_foundry.model import session

    historical = "Film Foundry / Electronic Negative Factory"
    assert PROJECT_NAME == historical
    assert film_foundry.__app_name__ == half_frame_darkroom.__app_name__ == historical
    assert film_foundry.__display_name__ == half_frame_darkroom.__display_name__ == APP_DISPLAY_NAME == "Film Foundry"
    assert sidecar.PROJECT_NAME == provenance.PROJECT_NAME == session.PROJECT_NAME == PROJECT_NAME
    assert historical in KNOWN_PROJECT_NAMES
    assert APP_DISPLAY_NAME in KNOWN_PROJECT_NAMES


def test_legacy_half_frame_darkroom_imports_are_the_same_modules():
    import film_foundry.app.cli as canonical_cli
    import film_foundry.core.engine as canonical_engine
    import half_frame_darkroom.app.cli as legacy_cli
    import half_frame_darkroom.core.engine as legacy_engine

    assert canonical_engine is legacy_engine
    assert canonical_cli is legacy_cli
    assert canonical_engine.develop_negative is legacy_engine.develop_negative
    assert canonical_engine.scan_negative is legacy_engine.scan_negative
    assert canonical_cli.main is legacy_cli.main
    assert canonical_engine.develop_medium is canonical_engine.develop_negative
    assert canonical_engine.scan_medium is canonical_engine.scan_negative
