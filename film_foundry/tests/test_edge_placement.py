from dataclasses import replace
import numpy as np
from film_foundry.contracts.edge_marks import block_bar_pattern,EdgeMarkPattern
from film_foundry.core.edge_direction import advance_stencil
from film_foundry.core.film_border import resolve_film_border_layout
from film_foundry.core.formed_film_border import _strip_masks
from film_foundry.model.config import FilmBorderConfig,DarkroomConfig


def test_block_sequences_and_direction_symbols_are_stable():
    for style in ('blocks','staggered_blocks','paired_blocks'):
        pattern=block_bar_pattern(123,8,style,42)
        assert pattern==block_bar_pattern(123,8,style,42)
        assert pattern!=block_bar_pattern(123,9,style,42)
        assert pattern!=block_bar_pattern(123,8,style,43)
        assert pattern!=block_bar_pattern(124,8,style,42)
        if style=='blocks':
            other=block_bar_pattern(123,9,style,42)
            for x in range(pattern.width):
                assert all(pattern.ink(x,y)==pattern.ink(x,0)==other.ink(x,y) for y in range(6))
                assert all(pattern.ink(x,y)==pattern.ink(x,6) for y in range(6,12))
            assert [pattern.ink(x,0) for x in range(16,112)]==[True,True,False,False]*24
        else:
            for x in range(pattern.width):assert all(pattern.ink(x,y)==pattern.ink(x,0) for y in range(12))
    for style in ('arrow','double_arrow','chevron','triangle','outline_triangle','outline_pentagon'):
        right=advance_stencil(None,style,'right',64,64)
        np.testing.assert_array_equal(advance_stencil(None,style,'left',64,64),right[:,::-1])
        assert right.max()==1


def test_loading_offset_uses_adjacent_frame_and_clips_each_rebate():
    cfg=FilmBorderConfig(enabled=True,strip_layout='frame_layout_v1',factory_layout='text_code',factory_barcode='staggered_blocks',
        factory_barcode_seed=42,frame_number=8,advance_marker_style='triangle')
    layout=resolve_film_border_layout(cfg,(160,800,3))
    base,holes=_strip_masks(cfg,layout,'FF 400')
    previous,_=_strip_masks(replace(cfg,frame_number=7),layout,'FF 400')
    shifted,shifted_holes=_strip_masks(replace(cfg,loading_offset=.125),layout,'FF 400')
    np.testing.assert_array_equal(shifted[:,100:],base[:,:-100])
    np.testing.assert_array_equal(shifted[:,:100],previous[:,-100:])
    np.testing.assert_array_equal(shifted_holes,np.roll(holes,100,axis=1))
    moved,_=_strip_masks(replace(cfg,factory_offset_top=(.2,1.)),layout,'FF 400')
    rows=base.shape[0]//2
    assert not moved[:rows].any()
    np.testing.assert_array_equal(moved[rows:],base[rows:])
    close_to_gate=replace(cfg,frame_number=None,advance_marker_style='none',factory_mark_type='laser',factory_mark_spread=3.,
        factory_offset_top=(0.,.9),factory_pattern_linked=False,factory_pattern_bottom=EdgeMarkPattern(1,1,b'\x00').encode())
    local,_=_strip_masks(close_to_gate,resolve_film_border_layout(close_to_gate,(160,800,3)),'FF 400')
    assert local[:rows].any() and not local[rows:].any()


def test_placement_freezes_before_development_and_scan_is_read_only(tmp_path):
    from film_foundry.core.engine import expose_image_to_latent,develop_negative_from_latent,scan_medium_output
    from film_foundry.core.latent_io import save_exposed_latent,load_exposed_latent,latent_identity_from_film
    cfg=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False,seed_strategy='fixed')
    cfg.film_border=FilmBorderConfig(enabled=True,factory_layout='text_code',factory_barcode='blocks',
        factory_barcode_seed=18,loading_offset=-.2,frame_number=8,advance_marker_style='outline_triangle',
        factory_offset_top=(.1,0.),advance_marker_offset=(-.1,-.15))
    source=np.full((48,240,3),.3,np.float32);latent=expose_image_to_latent(source,cfg)
    path=tmp_path/'exposure.darkroom_latent.npz';save_exposed_latent(path,latent,identity=latent_identity_from_film(cfg.film))
    loaded,_=load_exposed_latent(path)
    cfg.film_border.loading_offset=.4;cfg.film_border.factory_barcode_seed=999
    medium=develop_negative_from_latent(loaded,cfg)
    frozen=medium.metadata['formed_film_rebate']['config']
    assert frozen['loading_offset']==-.2 and frozen['factory_barcode_seed']==18
    assert frozen['advance_marker_style']=='outline_triangle'
    original=medium.optical_density_rgb.copy()
    scan_medium_output(medium,cfg)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)


def test_dialog_drag_and_shuffle_keep_configuration_local():
    from film_foundry.ui.qt import _application,QtCore,QtGui
    from film_foundry.ui.film_border_dialog import FilmBorderDialog
    app=_application();original=FilmBorderConfig(enabled=True,factory_layout='text_code',factory_barcode='blocks',frame_number=8)
    dialog=FilmBorderDialog(original);dialog.show();app.processEvents()
    preview=dialog.preview;start=QtCore.QPointF(preview.width()/2,preview.height()/2)
    for event in (
        QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress,start,start,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier),
        QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove,start+QtCore.QPointF(30,0),start+QtCore.QPointF(30,0),QtCore.Qt.MouseButton.NoButton,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier),
        QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonRelease,start,start,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.MouseButton.NoButton,QtCore.Qt.KeyboardModifier.NoModifier),
    ):app.sendEvent(preview,event)
    assert dialog.value().loading_offset>0
    dialog.align.click()
    assert dialog.value().loading_offset==0
    dialog.shuffle_blocks();assert dialog.value().factory_barcode_seed!=0
    dialog.reject()
    assert original.loading_offset==0 and original.factory_barcode_seed==0


def test_registered_strip_joins_the_adjacent_code_with_printed_number_off():
    from film_foundry.app.workspace_jobs import numbered_photo_config
    config = DarkroomConfig()
    config.film_border = FilmBorderConfig(enabled=True, factory_layout="text_code",
        factory_barcode="code128c", factory_batch=123)
    numbered = replace(config.film_border, frame_number=12, advance_marker_style="outline_pentagon")
    layout = resolve_film_border_layout(numbered,(180,640))
    base, _ = _strip_masks(numbered,layout,"FF 400")
    displaced = replace(numbered, factory_offset_bottom=(.8,0), advance_marker_offset=(-.5,0))
    np.testing.assert_array_equal(base,_strip_masks(displaced,layout,"FF 400")[0])
    bottom = base[layout.image_rect[1]:]
    assert bottom[:,:525].any() and not bottom[:,525:537].any() and bottom[:,537:].any()
    def strips(slot, dx):
        border = numbered_photo_config(config, slot).film_border
        assert border.frame_number is None and border.strip_frame_index == slot
        moved = replace(border, loading_offset=dx, factory_offset_bottom=(.65,0.))
        layout = resolve_film_border_layout(moved,(180,640))
        return _strip_masks(moved,layout,"FF 400")[0][layout.image_rect[1]:]
    for dx, neighbour in ((-.25,3),(.25,1)):
        baseline = strips(2,0.)
        adjacent = strips(neighbour,0.)
        reference = np.concatenate((baseline,adjacent) if dx<0 else (adjacent,baseline), axis=1)
        start = round(((-dx) if dx<0 else (1-dx)) * 640)
        np.testing.assert_array_equal(strips(2,dx),reference[:,start:start+640])
    for fmt,count in (("135",16),("half_frame",8),("120",0),("110",1)):
        assert len(resolve_film_border_layout(replace(config.film_border,format_key=fmt),(200,640)).sprocket_holes) == count
    old = DarkroomConfig.from_dict({"film_border":{"enabled":True,"format_key":"110"}})
    assert len(resolve_film_border_layout(old.film_border,(200,640)).sprocket_holes)==12
