"""Offline release-origin verification. No telemetry or authorization behavior."""


def get_release_marker():
    try:
        from ._release_marker import RELEASE_MARKER
    except ImportError:
        return None
    return RELEASE_MARKER
