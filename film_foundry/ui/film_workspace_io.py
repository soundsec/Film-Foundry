"""Film source/recipe files using the shared background workspace lifecycle."""
from copy import deepcopy
from pathlib import Path

from film_foundry.app.film_workspace import snapshot_film_workspace, save_film_workspace, load_film_workspace
from film_foundry.app.workflow import iter_images, iter_latent_files, iter_developed_medium_files
from film_foundry.ui.material_workspace_io import MaterialWorkspaceFiles
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtWidgets, ObservableValue


def _capture_controls(owner):
    """View memento also works when a current preset file has disappeared."""
    return {
        'values': {key: value.get() for key, value in vars(owner).items() if isinstance(value, ObservableValue)},
        'state': {key: deepcopy(getattr(owner, key)) for key in (
            '_recipe_base', '_profiles', '_frame_identity', '_path_seed_offsets',
            '_principle_chemistry', '_expert_operating_mode', '_legacy_effect_controls', '_effect_intent_model')},
        'labels': {key: value.get() for key, value in owner._profile_labels.items()},
        'curves': {key: editor.read() for key, editor in owner.curve_editors.items()},
        'mixer': owner.color_mixer.read(),
    }


def _restore_controls(owner, previous):
    for key, value in previous['state'].items(): setattr(owner, key, value)
    for key, value in previous['values'].items(): getattr(owner, key).set(value)
    for key, value in previous['labels'].items(): owner._profile_labels[key].set(value)
    for key, value in previous['curves'].items(): owner.curve_editors[key].load(value)
    owner.color_mixer.load(previous['mixer'])
    owner._update_curve_summary()
    owner.expert_operation.blockSignals(True)
    owner.expert_operation.setCurrentIndex(owner.expert_operation.findData(owner._expert_operating_mode))
    owner.expert_operation.blockSignals(False)
    owner._update_mode_visibility()


class FilmWorkspaceFiles(MaterialWorkspaceFiles):
    def __init__(self, owner):
        super().__init__(owner)
        self.source_names = {}

    def title(self):
        return text('胶片工作', 'Film work')

    def restored_path(self, path):
        return Path(path).absolute()

    def save(self, *, save_as=False):
        if not self._ready(): return False
        path = self.path
        if path is None or save_as:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self.owner.root.qt,
                text('保存胶片工作目录包', 'Save film work package'), str(path or 'film.fffilm'), 'Film work (*.fffilm)')
            if not path: return False
            if not str(path).lower().endswith('.fffilm'): path = str(path) + '.fffilm'
        return self.save_to(path)

    def open(self):
        if not self._ready(): return False
        path = QtWidgets.QFileDialog.getExistingDirectory(self.owner.root.qt,
            text('选择 .fffilm 胶片工作目录', 'Choose a .fffilm directory'), str(self.path.parent if self.path else ''))
        return self.open_from(path) if path else False

    def save_to(self, path):
        if not self._ready(): return False
        owner = self.owner
        try:
            if owner._active_roll is not None:
                raise ValueError(text('整卷请使用电子胶卷存取；胶片工作包保存当前照片或介质来源。',
                    'Use electronic film rolls for roll sessions; this package saves photo or medium sources.'))
            mode = owner.pipeline_mode.get()
            if mode in ('full', 'develop', 'expose'):
                paths = (owner._selected_photos if mode == 'full' and owner.input_path.get() in owner._selected_photos
                         else iter_images(Path(owner.input_path.get())))
                active_path = owner.input_path.get()
            else:
                iterator = iter_latent_files if mode == 'develop_latent' else iter_developed_medium_files
                paths = iterator(Path(owner.negative_path.get())); active_path = owner.negative_path.get()
            paths = [Path(p).absolute() for p in paths]
            active = paths.index(Path(active_path).absolute()) if Path(active_path).absolute() in paths else 0
            workspace = snapshot_film_workspace(owner._session_payload(), paths, active=active,
                layout=owner._workspace_layout,
                source_names=[self.source_names.get(str(p.resolve()), p.name) for p in paths])
        except Exception as exc:
            self._error(str(exc)); return False
        return self._start('save', path, lambda: save_film_workspace(path, workspace))

    def open_from(self, path, *, _approved=False):
        if not self._ready(): return False
        if hasattr(self.owner,'work_sessions') and not _approved:
            return self.owner.work_sessions.protect(lambda:self.open_from(path,_approved=True),pages=[self.owner.film_workspace])
        return self._start('open', path, lambda: load_film_workspace(path))

    def _publish(self, workspace):
        owner = self.owner
        # Parse/read the whole package before touching the live controls. Keep a
        # view memento too: even an unresolved preset must not block opening.
        previous = _capture_controls(owner)
        photos, roll, layout = list(owner._selected_photos), owner._active_roll, owner._workspace_layout
        names = deepcopy(self.source_names)
        ready = owner._auto_preview_ready; owner._auto_preview_ready = False
        try:
            owner._active_roll = None
            owner._apply_session_payload(workspace.session)
            owner._selected_photos = [str(p) for p in workspace.sources] if workspace.session['pipeline_mode'] == 'full' else []
            owner._sync_selected_photos()
            owner.set_workspace_layout(workspace.layout, persist=False)
            self.source_names = dict(zip((str(p.resolve()) for p in workspace.sources), workspace.source_names))
            owner._preview_session.clear()
            owner._sync_activity_ui()
            owner.show_workbench()
        except Exception:
            owner._active_roll = roll
            _restore_controls(owner, previous)
            owner._selected_photos = photos; owner._sync_selected_photos()
            owner.set_workspace_layout(layout, persist=False)
            self.source_names = names
            owner._sync_activity_ui()
            raise
        finally:
            owner._auto_preview_ready = ready
        owner._preview_edited()
