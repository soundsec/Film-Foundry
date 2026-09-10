import cv2
import pytest

from film_foundry.core.runtime_resources import (
    configure_native_thread_limit,
    resolve_native_thread_limit,
    startup_opencv_threads,
)


def test_native_thread_limit_is_portable_and_zero_restores_startup_default():
    try:
        assert resolve_native_thread_limit(1) == 1
        assert configure_native_thread_limit(1) == 1
        restored = configure_native_thread_limit(0)
        assert restored == resolve_native_thread_limit(0)
        assert restored <= startup_opencv_threads()
    finally:
        configure_native_thread_limit(0)


def test_native_thread_limit_rejects_negative_values():
    with pytest.raises(ValueError, match="zero or positive"):
        resolve_native_thread_limit(-1)


def test_native_thread_configuration_matches_opencv_runtime():
    try:
        effective = configure_native_thread_limit(2)
        assert effective == int(cv2.getNumThreads())
        assert 1 <= effective <= 2
    finally:
        configure_native_thread_limit(0)
