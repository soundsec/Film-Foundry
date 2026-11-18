"""Admission for independent desktop image workers. Engine parallelism stays internal."""
from contextlib import contextmanager
from enum import IntEnum
from itertools import count
from threading import Condition

from film_foundry.core.computation_control import cancellation_scope


class Priority(IntEnum):
    INTERACTIVE = 0
    STRIP = 1
    THUMBNAIL = 2


class PreviewBudget:
    def __init__(self):
        self.condition = Condition()
        self.waiting = set()
        self.sequence = count()
        self.active = False

    @contextmanager
    def slot(self, priority, check=None):
        with self.condition:
            ticket = (priority, next(self.sequence))
            self.waiting.add(ticket)
            try:
                while self.active or ticket != min(self.waiting):
                    if check:
                        check()
                    self.condition.wait(.1 if check else None)
                if check:
                    check()
                self.active = True
            finally:
                self.waiting.remove(ticket)
                self.condition.notify_all()
        try:
            with cancellation_scope(check):
                yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()

    def run(self, priority, action, *args, check=None):
        with self.slot(priority, check):
            return action(*args)


preview_budget = PreviewBudget()
