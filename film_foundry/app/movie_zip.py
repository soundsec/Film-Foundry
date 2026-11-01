"""Portable material-master archive export; no observation or re-encoding."""
from __future__ import annotations

import os
from pathlib import Path
import time
from zipfile import ZipFile, ZIP_STORED

from film_foundry.app.control import WorkflowControl
from film_foundry.app.master_workspace import master_workspace, check_space
from film_foundry.app.movie_master import read_master
from film_foundry.contracts.movie_master import MANIFEST


def export_master_zip(source: Path, output: Path, *, control: WorkflowControl | None = None) -> Path:
    control = control or WorkflowControl()
    root, document = read_master(source)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Choose a new material master ZIP filename: '+str(output))
    if output.is_relative_to(root):
        raise ValueError('Export the archive outside its source material master.')
    with master_workspace(output.parent,control) as pending:
        archive = pending/'master.zip'
        last_check = time.monotonic() - 1
        with ZipFile(archive,'w',compression=ZIP_STORED,allowZip64=True) as zip:
            for name in [MANIFEST]+[frame['path'] for frame in document['frames']]:
                control.check_cancel()
                check_space(pending)
                with (root/name).open('rb') as source_file, zip.open(name,'w',force_zip64=True) as target:
                    while True:
                        control.check_cancel()
                        now = time.monotonic()
                        if now-last_check >= .25:
                            check_space(pending);last_check = now
                        block = source_file.read(64*1024)
                        if not block:
                            break
                        target.write(block)
        with archive.open('r+b') as stream:
            os.fsync(stream.fileno())
        control.check_cancel()
        check_space(pending)
        if output.exists():
            raise FileExistsError(output)
        os.rename(archive,output)
    return output
