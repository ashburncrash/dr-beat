"""Tiny stdlib HTTP helper shared by the Spotify and lyrics clients.

Every client takes an injectable `http(method, url, headers, data)` callable with
this signature, so tests can run fully offline.
"""
import urllib.error
import urllib.request

USER_AGENT = "song-matcher/0.2 (personal project; stdlib urllib)"


def request(method, url, headers=None, data=None, timeout=10):
    """Return (status, headers_dict, body_bytes). Never raises on HTTP status codes."""
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def header(headers, name):
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None
