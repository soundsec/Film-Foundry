"""Disposable, bounded thumbnail bytes. SQLite keys are metadata, never image hashes."""
import json
import os
from pathlib import Path
import sqlite3
from threading import local
import time
from contextlib import contextmanager

from film_foundry.app.paths import app_root


def cache_path():
    override = os.environ.get('FILM_FOUNDRY_UI_SETTINGS')
    settings = Path(override) if override else app_root() / 'user_presets' / 'desktop_ui.json'
    return settings.with_name(settings.stem + '_thumbnails.sqlite3')


class _ThreadConnection(local):
    """Keep at most one database open on each worker thread."""
    path = None
    db = None


_connections = _ThreadConnection()
_TOUCH_INTERVAL = 60.0


FACTORY_THUMBNAILS = Path(__file__).resolve().parents[1] / 'assets' / 'selection_examples.sqlite3'
_examples = local()


def factory_thumbnail(key):
    """Read shipped examples only when the full reference recipe matches."""
    try:
        if getattr(_examples, 'db', None) is None:
            uri = FACTORY_THUMBNAILS.as_uri() + '?mode=ro&immutable=1'
            _examples.db = sqlite3.connect(uri, uri=True)
        row = _examples.db.execute('SELECT image FROM thumbnails WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
    except (OSError, sqlite3.Error):
        return None


class ThumbnailCache:
    def __init__(self, path=None, *, max_bytes=128*1024*1024, max_entries=2048):
        self.path = Path(path) if path else cache_path()
        self.max_bytes = max_bytes; self.max_entries = max_entries

    @staticmethod
    def close_thread_connection():
        if _connections.db is not None:
            _connections.db.close()
            _connections.path = _connections.db = None

    @contextmanager
    def connect(self):
        path = self.path.resolve()
        if _connections.path != path:
            self.close_thread_connection()
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, timeout=.25)
            try:
                db.execute('PRAGMA auto_vacuum=FULL')
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA journal_size_limit=1048576')
                with db:
                    db.execute('CREATE TABLE IF NOT EXISTS thumbnails (key TEXT PRIMARY KEY, image BLOB NOT NULL, used REAL NOT NULL)')
                    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, generation INTEGER NOT NULL)')
                    db.execute('INSERT OR IGNORE INTO state VALUES (1, 0)')
            except BaseException:
                db.close()
                raise
            _connections.db, _connections.path = db, path
        try:
            with _connections.db as db:
                yield db
        except sqlite3.DatabaseError:
            self.close_thread_connection()
            raise

    @staticmethod
    def key(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

    def lookup(self, key):
        """Return bytes and a generation ticket; cache errors simply mean a miss."""
        try:
            with self.connect() as db:
                generation, data, used = db.execute(
                    'SELECT generation, image, used FROM state LEFT JOIN thumbnails ON key=? WHERE id=1',
                    (key,)).fetchone()
                now = time.time()
                if used is not None and now - used >= _TOUCH_INTERVAL:
                    db.execute('UPDATE thumbnails SET used=? WHERE key=? AND used=?', (now, key, used))
                return data, generation
        except (OSError, sqlite3.Error):return None, None

    def put(self, key, data, generation):
        if generation is None or len(data)>self.max_bytes:return
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT generation FROM state WHERE id=1').fetchone()[0]!=generation:return
                db.execute('INSERT OR REPLACE INTO thumbnails VALUES (?,?,?)', (key,data,time.time()))
                count,size=db.execute('SELECT COUNT(*),COALESCE(SUM(length(image)+length(CAST(key AS BLOB))),0) FROM thumbnails').fetchone()
                if count>self.max_entries or size>self.max_bytes:
                    for old,n in db.execute('SELECT key,length(image)+length(CAST(key AS BLOB)) FROM thumbnails ORDER BY used').fetchall():
                        db.execute('DELETE FROM thumbnails WHERE key=?',(old,));count-=1;size-=n
                        if count<=self.max_entries and size<=self.max_bytes:break
        except (OSError, sqlite3.Error):pass

    def clear(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE state SET generation=generation+1 WHERE id=1')
            db.execute('DELETE FROM thumbnails')

    def statistics(self):
        try:
            with self.connect() as db:
                count,size=db.execute('SELECT COUNT(*),COALESCE(SUM(length(image)+length(CAST(key AS BLOB))),0) FROM thumbnails').fetchone()
        except (OSError, sqlite3.Error):count,size=0,0
        return dict(entries=count,bytes=size,limit=self.max_bytes)
