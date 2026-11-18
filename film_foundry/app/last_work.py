"""Atomic settings recovery with a configurable generation limit."""
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Thread

from film_foundry.app.desktop_settings import settings_path
from film_foundry.core.atomic_io import atomic_output_path, strict_json_load

MAX_BYTES = 4 * 1024 * 1024


def recovery_directory():
    return settings_path().parent / 'desktop_recovery'


class LastWorkStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else recovery_directory()
        self.generations = 1
        self.interval_minutes = 5
        self.path = self.directory / 'last_work.json'

    def read(self, path=None):
        path = self.path if path is None else path
        if path.stat().st_size > MAX_BYTES * 5 + 1024:
            raise ValueError('Recovery settings exceed the size limit')
        value = strict_json_load(path)
        if not isinstance(value, dict) or value.get('model') != 'desktop_last_work_v1':
            raise ValueError('Unsupported last-work settings')
        if not isinstance(value.get('work'), dict):
            raise ValueError('Invalid last-work settings')
        return value

    def records(self):
        current = self.read()
        latest = {'saved_at': current.get('saved_at'), 'work': current['work']}
        previous = current.get('previous', [])
        records = [latest]
        if isinstance(previous, list):
            for item in previous[:4]:
                if isinstance(item, dict) and isinstance(item.get('work'), dict) and isinstance(item.get('saved_at'), str):
                    records.append({'saved_at': item['saved_at'], 'work': item['work']})
        return records

    def archive_unrecovered(self):
        from film_foundry.core.atomic_io import atomic_write_json
        record = self.read()
        stamp = self.path.stat().st_mtime_ns
        path = self.directory / f'unrecovered-{stamp}.json'
        if not path.exists():
            atomic_write_json(path, record)

    def archives(self):
        return sorted(self.directory.glob('unrecovered-*.json'), reverse=True)

    def write(self, work):
        value = dict(model='desktop_last_work_v1',
                     saved_at=datetime.now(timezone.utc).isoformat(), work=work)
        # Intentionally no array encoder, pickle, or conversion of media to lists.
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(data) > MAX_BYTES:
            raise ValueError('Recovery settings exceed the size limit')
        limit = self.generations
        if limit in (3, 5) and self.path.is_file():
            try:
                previous = self.records()
            except (ValueError, KeyError, TypeError):
                previous = []
            if previous:
                head, *history = previous
                if history:
                    try:
                        elapsed = datetime.fromisoformat(head['saved_at']) - datetime.fromisoformat(history[0]['saved_at'])
                        rotate = elapsed.total_seconds() >= 60 * self.interval_minutes
                    except (ValueError, TypeError):
                        rotate = True
                else:
                    rotate = True
                if rotate and head['work'] != work:
                    history.insert(0, head)
                value['previous'] = history[:limit-1]
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        with atomic_output_path(self.path) as temporary:
            with temporary.open('wb') as handle:
                handle.write(data)
                handle.flush()
                import os
                os.fsync(handle.fileno())


class LatestWorkWriter:
    """At most one running and one latest pending settings snapshot."""
    def __init__(self, store, completed):
        self.store = store
        self.completed = completed
        self.condition = Condition()
        self.pending = None
        self.stopping = False
        self.thread = Thread(target=self._run, name='last-work-writer', daemon=True)
        self.thread.start()

    def submit(self, work):
        with self.condition:
            if self.stopping:
                return
            self.pending = work
            self.condition.notify()

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or self.stopping)
                if self.pending is None:
                    return
                work, self.pending = self.pending, None
            error = None
            try:
                self.store.write(work)
            except Exception as exc:
                from film_foundry.app.crash_journal import record_exception
                record_exception('Automatic last-work save', exc)
                error = str(exc)
            self.completed(error)
            # Do not retain the preceding snapshot while waiting for another.
            del work
