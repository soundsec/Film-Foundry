"""Bounded local diagnostics without frame locals, images or recipe dumps."""
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading

_logger = logging.getLogger('film_foundry.desktop')
_logger.addHandler(logging.NullHandler())
_logger.propagate = False


def record_exception(context, error):
    _logger.error('%s', context, exc_info=(type(error), error, error.__traceback__))


class CrashJournal:
    def __init__(self, directory):
        self.directory = directory
        self.handler = None
        self.fault_file = None

    def install(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(self.directory / 'desktop.log', maxBytes=1024*1024,
                                           backupCount=2, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        _logger.addHandler(self.handler)
        self.old_sys, self.old_thread = sys.excepthook, threading.excepthook
        def unhandled(kind, error, trace):
            _logger.critical('Unhandled main/UI exception', exc_info=(kind, error, trace))
            self.old_sys(kind, error, trace)
        def thread_error(args):
            _logger.critical('Unhandled worker exception',
                             exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
            self.old_thread(args)
        self.sys_hook, self.thread_hook = unhandled, thread_error
        sys.excepthook, threading.excepthook = unhandled, thread_error
        self.had_fault_handler = faulthandler.is_enabled()
        if not self.had_fault_handler:
            path = self.directory / 'native-fault.log'
            if path.exists() and path.stat().st_size > 1024*1024:
                path.replace(path.with_suffix('.previous.log'))
            self.fault_file = path.open('a', encoding='utf-8')
            faulthandler.enable(self.fault_file, all_threads=True)
        return self

    def close(self):
        if self.handler is None:
            return
        if sys.excepthook is self.sys_hook:
            sys.excepthook = self.old_sys
        if threading.excepthook is self.thread_hook:
            threading.excepthook = self.old_thread
        if self.fault_file is not None:
            faulthandler.disable()
            self.fault_file.close()
        _logger.removeHandler(self.handler)
        self.handler.close()
        self.handler = None
