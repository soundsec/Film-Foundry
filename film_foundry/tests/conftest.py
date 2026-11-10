"""Never read a developer's desktop work history or open save prompts in tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_desktop_work_policy(tmp_path,monkeypatch):
    path=tmp_path/'desktop_test_policy.json'
    path.write_text('{"leave_policy":"discard"}',encoding='utf-8')
    monkeypatch.setenv('FILM_FOUNDRY_DESKTOP_SETTINGS',str(path))
