"""Process-wide native-library resource policy.

Film Foundry is normally run as a dedicated desktop process, but the machine
may still be busy with unrelated work.  OpenCV otherwise defaults to every
logical CPU on many installations, which can increase contention and make
wall-clock latency less predictable.  This module applies an explicit thread
budget without changing image dimensions, numerical precision, or operators.
"""

from __future__ import annotations

import os

import cv2


_STARTUP_OPENCV_THREADS = max(1, int(cv2.getNumThreads()))


def resolve_native_thread_limit(limit: int) -> int:
    """Resolve a configured limit against the current machine.

    Zero means the OpenCV setting observed when this module was imported.
    Positive values are capped by the available logical CPU count so a preset
    remains portable to smaller machines.
    """

    normalized = int(limit)
    if normalized < 0:
        raise ValueError("processing.native_thread_limit must be zero or positive.")
    requested = _STARTUP_OPENCV_THREADS if normalized == 0 else normalized
    available = os.cpu_count()
    if available is not None and available > 0:
        requested = min(requested, int(available))
    return max(1, int(requested))


def configure_native_thread_limit(limit: int) -> int:
    """Apply and return the effective OpenCV worker-thread limit.

    OpenCV exposes this as a process-wide setting.  Film Foundry therefore
    treats it as a runtime resource policy rather than an image-model field.
    Reapplying the same value is skipped.
    """

    resolved = resolve_native_thread_limit(limit)
    if int(cv2.getNumThreads()) != resolved:
        cv2.setNumThreads(resolved)
    return int(cv2.getNumThreads())


def startup_opencv_threads() -> int:
    """Return the OpenCV thread count captured before Film Foundry policy."""

    return _STARTUP_OPENCV_THREADS
