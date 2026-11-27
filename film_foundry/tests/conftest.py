"""Never read a developer's desktop work history or open save prompts in tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_desktop_work_policy(tmp_path,monkeypatch):
    directory=tmp_path.parent/'desktop-policies'
    directory.mkdir(exist_ok=True)
    path=directory/(tmp_path.name+'.json')
    path.write_text('{"leave_policy":"discard"}',encoding='utf-8')
    monkeypatch.setenv('FILM_FOUNDRY_DESKTOP_SETTINGS',str(path))
