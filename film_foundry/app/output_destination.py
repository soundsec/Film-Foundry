"""Default user result location; merely describing it creates no files."""
from datetime import datetime
from .paths import app_root


def default_output_directory(*, date=None):
    from .desktop_settings import load_settings
    from pathlib import Path
    return Path(load_settings()['workspace']) / 'outputs' / (date or datetime.now().strftime('%Y-%m-%d'))
