from film_foundry.tests.test_desktop_usability import desktop,photos


def test_positive_pairing_and_explicit_scanner_roles(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film')
    panel.auto_develop.set(True);panel.link_scanner.set(True)
    panel._set_material_ui_from_key('color_reversal_amber')
    label=next(label for label,key in panel._film_display_key_map.items() if key=='color_reversal_amber')
    panel.film_preset_display.set(label);panel._on_film_preset_display_selected()
    assert panel.scanner_preset.get()=='positive_transparency_warm'
    assert not panel.scan_invert.get() and not panel.scan_remove_base.get()
    for name in ('warm_consumer_scan','positive_transparency_scan'):
        panel.scan_invert.set(True);panel.scan_remove_base.set(True)
        label=next(label for label,key in panel._scanner_display_key_map.items() if key==name)
        panel.scanner_preset_display.set(label);panel._on_scanner_preset_display_selected()
        assert not panel.link_scanner.get()
        assert panel.scan_invert.get()==panel.scan_remove_base.get()==(name=='warm_consumer_scan')
    panel.scan_remove_base.set(True)
    assert panel._config().scanner.remove_base_mask
    # A custom negative bath wins over the selected reversal stock identity.
    panel.expert_mode.set(True);panel._expert_operating_mode='principle'
    panel._principle_chemistry=panel._config().chemistry
    panel._principle_chemistry.program_key='color_negative'
    panel._principle_chemistry.process_steps=None
    panel.link_scanner.set(True);panel._apply_matching_scanner()
    assert panel.scanner_preset.get()=='neutral_scan'
    assert panel.scan_invert.get() and panel.scan_remove_base.get()
