import copy
import os
import threading
import time

try:
    from .db import using_cloudflare_d1
except ImportError:
    from db import using_cloudflare_d1  # type: ignore


_LOCK = threading.Lock()
_CACHE = {}


def _is_enabled():
    if not using_cloudflare_d1():
        return False
    return (os.getenv('ENABLE_RUNTIME_CACHE') or '1').strip().lower() not in {'0', 'false', 'no'}


def get_cache_ttl(default_seconds=5):
    raw_value = (os.getenv('RUNTIME_CACHE_TTL_SECONDS') or '').strip()
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            return default_seconds
    return default_seconds


def get_cached(key):
    if not _is_enabled():
        return None

    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        if entry['expires_at'] <= now:
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(entry['value'])


def set_cached(key, value, ttl_seconds=None):
    if not _is_enabled():
        return value

    expires_at = time.monotonic() + max(1, int(ttl_seconds or get_cache_ttl()))
    with _LOCK:
        _CACHE[key] = {
            'value': copy.deepcopy(value),
            'expires_at': expires_at
        }
    return value


def invalidate_cache(*keys):
    with _LOCK:
        for key in keys:
            _CACHE.pop(key, None)


def invalidate_cache_prefix(prefix):
    with _LOCK:
        for key in list(_CACHE.keys()):
            if key.startswith(prefix):
                _CACHE.pop(key, None)
