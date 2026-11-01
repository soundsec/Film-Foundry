import os
from zipfile import ZipFile
import time

import pytest

from film_foundry.app.control import WorkflowControl, WorkflowCancelled
from film_foundry.app.movie_master import expose_movie_frames, read_master
from film_foundry.app.movie_zip import export_master_zip
from film_foundry.tests.test_movie_master import frames, recipe


def test_export_cli_and_archive_preserve_frames_and_manifest(tmp_path):
    from film_foundry.app.cli import main
    root=expose_movie_frames(frames(),tmp_path/'source',recipe())
    output=tmp_path/'master.zip'
    assert main(['movie-export',str(root),str(output)]) == 0
    _,document=read_master(root)
    names=['master.json']+[f['path'] for f in document['frames']]
    with ZipFile(output) as zip:
        assert zip.namelist() == names
        for name in names: assert zip.read(name) == (root/name).read_bytes()
    with pytest.raises(FileExistsError): export_master_zip(root,output)
    with pytest.raises(ValueError): export_master_zip(root,root/'invalid.zip')
    checks=0
    def cancelled():
        nonlocal checks
        checks+=1
        return checks > 5
    with pytest.raises(WorkflowCancelled):
        export_master_zip(root,tmp_path/'cancel.zip',control=WorkflowControl(cancel_requested=cancelled))
    assert not (tmp_path/'cancel.zip').exists()


def test_desktop_export_action_writes_archive_without_switching_to_processing(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from film_foundry.ui.qt import _application
    from film_foundry.ui.movie_master_dialog import MovieMasterDialog
    app=_application()
    source=expose_movie_frames(frames(),tmp_path/'source',recipe())
    dialog=MovieMasterDialog(recipe());dialog.mode.setCurrentIndex(3)
    target=tmp_path/'from-dialog.zip'
    dialog.source.setText(str(source));dialog.output.setText(str(target));dialog.run()
    deadline=time.monotonic()+5
    while dialog.running and time.monotonic() < deadline:
        app.processEvents();time.sleep(.005)
    assert not dialog.running and target.is_file() and dialog.mode.currentIndex() == 3
    assert dialog.source.text() == str(source)
    dialog.reject();dialog.deleteLater();app.processEvents()
