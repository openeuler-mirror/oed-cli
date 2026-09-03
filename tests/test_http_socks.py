"""Regression for issue #5 (atomgit openeuler/oed-cli): a SOCKS proxy in the
environment makes ``httpx.Client()`` raise ``ImportError`` (missing
``socksio``) at construction time — not an ``httpx.HTTPError``, so it used to
escape as a bare traceback. ``get_request`` must convert it to a readable
:class:`UserError` pointing at the ``socks`` extra.
"""

import httpx
import pytest

from oed_cli import http as http_mod
from oed_cli.errors import UserError


def _install_socks_importerror(monkeypatch):
    """Make ``httpx.Client`` raise the socksio ImportError a real env triggers."""

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "Using SOCKS proxy, but the 'socksio' package is not installed. "
                "Make sure to install httpx using `pip install httpx[socks]`."
            )

        def __enter__(self):
            ...

        def __exit__(self, *a):
            ...

    monkeypatch.setattr(httpx, "Client", _BoomClient)


def test_socks_missing_socksio_raises_readable_user_error(monkeypatch):
    _install_socks_importerror(monkeypatch)

    with pytest.raises(UserError) as exc_info:
        http_mod.get_request("GET", "https://example.invalid/x")

    err = exc_info.value
    assert err.kind == "socks_dependency_missing"
    assert "socksio" in err.message
    assert "httpx[socks]" in err.hint


def test_unrelated_importerror_still_propagates(monkeypatch):
    """An ImportError that is NOT about socksio must not be swallowed."""

    class _OtherBoomClient:
        def __init__(self, *args, **kwargs):
            raise ImportError("some unrelated optional thing is missing")

        def __enter__(self):
            ...

        def __exit__(self, *a):
            ...

    monkeypatch.setattr(httpx, "Client", _OtherBoomClient)

    with pytest.raises(ImportError, match="unrelated"):
        http_mod.get_request("GET", "https://example.invalid/x")
