"""Editing must retain recipe precision, automatic conditions and both drafts."""
from copy import deepcopy

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    for key in ('FILM_FOUNDRY_UI_SETTINGS', 'FILM_FOUNDRY_CAMERA_SETTINGS', 'FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key, str(tmp_path / (key + '.json')))
    from film_foundry.ui.qt import _application
    return _application()


def test_standard_and_custom_drafts_keep_automatic_agitation(app):
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    config = DarkroomConfig()
    original = deepcopy(config)
    dialog = RollProcessDialog(config, principle_only=False)
    try:
        dialog.controls['time_min'].setValue(10)
        assert dialog.controls['agitation_time_s'].value() == 600
        assert dialog.controls['agitation_time_s'].parentWidget().slider.value() == 10000
        assert dialog.condition_value('agitation_time_s') is None
        assert dialog.rows()[0]['time_min'] == 10
        dialog.custom.setChecked(True)
        dialog.table.setCurrentCell(0, 0)
        dialog.sequence_view.numbers['time_min'].setValue(6.75)
        assert dialog.sequence_view.numbers['agitation_time_s'].value() == 405
        assert dialog.rows()[0]['agitation_time_s'] is None
        draft = dialog.rows()
        dialog.custom.setChecked(False)
        assert dialog.rows()[0]['time_min'] == 10
        dialog.custom.setChecked(True)
        assert dialog.rows() == draft
        dialog.move(1)
        assert dialog.rows()[1] == draft[0]
        dialog.move(-1)
        dialog.apply()
        assert not dialog.status.text()
        assert dialog.config.chemistry.process_steps[0]['agitation_time_s'] is None
        damage = [row for row in dialog.config.chemistry.process_steps if row['action'] == 'destroy_dye']
        assert all(row['dye_destruction_basis'] == 'compatibility' for row in damage)
        assert config == original
    finally:
        dialog.close()


def test_native_damage_template_does_not_duplicate_global_request(app):
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    config = DarkroomConfig()
    config.chemistry.dye_damage_strength = .25
    dialog = RollProcessDialog(config, principle_only=False)
    try:
        dialog.custom.setChecked(True)
        dialog.apply()
        assert not dialog.status.text()
        recipe = dialog.config.chemistry
        program = program_from_develop_recipe(recipe)
        damage = [step for step in program.steps if step.action.value == 'destroy_dye']
        assert sum(step.dye_destruction_basis == 'explicit' for step in damage) == 1
        assert sum(step.dye_destruction_basis == 'compatibility' for step in damage) == 1
        assert recipe.dye_damage_strength == 0
        assert config.chemistry.dye_damage_strength == .25
    finally:
        dialog.close()


def test_existing_precision_and_invalid_table_do_not_lose_custom_draft(app):
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    config = DarkroomConfig()
    config.chemistry.process_steps = ({'action': 'develop_color', 'time_min': 8.123456789,
                                     'agitation_time_s': 12.345678901},)
    dialog = RollProcessDialog(config, principle_only=False)
    try:
        initial = dialog.rows()
        dialog.custom.setChecked(False)
        dialog.custom.setChecked(True)
        assert dialog.rows() == initial
        assert dialog.rows()[0]['time_min'] == 8.123456789
        dialog.table.item(0, 1).setText('invalid')
        dialog.custom.setChecked(False)
        assert dialog.custom.isChecked()
        assert not dialog.program.isEnabled()
        assert not dialog.native_button.isEnabled()
        assert all(button.isEnabled() for button in dialog.row_buttons)
        dialog.table.item(0, 1).setText('9')
        assert dialog.rows()[0]['agitation_time_s'] == 12.345678901
        dialog.sequence_view.numbers['agitation_time_s'].setValue(120)
        dialog.sequence_view.numbers['time_min'].setValue(8)
        assert dialog.rows()[0]['agitation_time_s'] == 120
    finally:
        dialog.close()


def test_solution_sliders_follow_enabled_state_and_preserve_precision(app):
    from film_foundry.contracts.bath_solution import MODEL, validate_solution
    from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
    value = validate_solution({'model': MODEL, 'action': 'develop_silver',
                               'rate_per_minute': .123456789123}, 'develop_silver')
    dialog = BathSolutionDialog('develop_silver', value)
    try:
        number = dialog.controls['rate_per_minute']
        row = number.parentWidget()
        dialog.enabled.setChecked(False)
        assert not row.slider.isEnabled()
        dialog.enabled.setChecked(True)
        assert row.slider.isEnabled()
        assert number.value() == .123457
        dialog.apply()
        assert dialog.value['rate_per_minute'] == value['rate_per_minute']
        row.slider.setValue(5000)
        assert number.value() == pytest.approx((number.minimum() + number.maximum()) / 2)
        number.setValue(number.minimum())
        assert row.slider.value() == 0
        dialog.formation_enabled.setChecked(True)
        assert all(control.parentWidget().slider.isEnabled() for control in dialog.formation_controls.values())
        dialog.formation_enabled.setChecked(False)
        assert all(not control.parentWidget().slider.isEnabled() for control in dialog.formation_controls.values())
    finally:
        dialog.close()
