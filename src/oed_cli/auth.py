"""Local secret storage for ``oed`` authentication tokens.

On Windows, tokens are encrypted at rest for the current Windows user via
DPAPI (``CryptProtectData`` / ``CryptUnprotectData`` through ctypes) — a real
encryption boundary with zero runtime dependencies. Non-Windows platforms fall
back to base64, which is documented obfuscation and NOT encryption.

The on-disk schema is versioned and keyed by ``service`` so a future general
``oed login`` can share this store with the ``ag``-specific token here.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import datetime as _dt
import json
import platform
from pathlib import Path
from typing import Any

from .discovery import _cache_dir
from .errors import UserError

_SCHEMA_VERSION = 1
_DPAPI_ENTROPY = b"oed-cli:ag:v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _is_windows() -> bool:
    return platform.system() == "Windows"


def token_path(service: str = "ag") -> Path:
    """Return the on-disk path for ``service``'s encrypted token.

    Lives under the shared cache dir (honors ``OED_CACHE_DIR``) in a ``tokens/``
    subdirectory so ``oed cache clear`` (which only unlinks ``discovery.json``)
    can never wipe credentials.
    """

    return _cache_dir() / "tokens" / f"{service}.json"


def _win32_crypt(data: bytes, *, protect: bool) -> bytes:
    """Protect/unprotect ``data`` with DPAPI scoped to the current Windows user."""

    windll = ctypes.windll
    func = windll.crypt32.CryptProtectData if protect else windll.crypt32.CryptUnprotectData
    func.argtypes = [
        ctypes.POINTER(_DATA_BLOB),  # pDataIn
        ctypes.c_wchar_p,            # szDataDescr
        ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy
        ctypes.c_void_p,             # pvReserved
        ctypes.c_void_p,             # pPromptStruct
        ctypes.c_ulong,              # dwFlags
        ctypes.POINTER(_DATA_BLOB),  # pDataOut
    ]
    func.restype = ctypes.c_int

    # keep the backing buffers referenced for the duration of the call
    inp = ctypes.create_string_buffer(data, len(data))
    in_blob = _DATA_BLOB(len(data), ctypes.cast(inp, ctypes.POINTER(ctypes.c_char)))
    ent = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
    ent_blob = _DATA_BLOB(len(_DPAPI_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()

    ok = func(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        side = "protect" if protect else "unprotect"
        raise UserError(
            f"Windows DPAPI could not {side} the token",
            kind="dpapi_failed",
            hint="Retry, or file an issue if it persists.",
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        windll.kernel32.LocalFree(out_blob.pbData)


def _encrypt(plain: str) -> tuple[str, str]:
    """Return ``(base64 blob, schema encryption marker)`` for ``plain``."""

    raw = plain.encode("utf-8")
    if _is_windows():
        return base64.b64encode(_win32_crypt(raw, protect=True)).decode("ascii"), "dpapi"
    return base64.b64encode(raw).decode("ascii"), "base64"


def store_token(token: str, *, service: str = "ag") -> Path:
    """Encrypt ``token`` and write it to disk for ``service``.

    Never stores plaintext; raises :class:`UserError` if the write fails.
    """

    blob, encryption = _encrypt(token)
    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "service": service,
        "encryption": encryption,
        "secret": blob,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    path = token_path(service)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise UserError(
            f"could not write token store {path}: {exc}",
            kind="token_store_write_failed",
            hint="Check the directory is writable, or set OED_CACHE_DIR.",
        ) from exc
    return path


def read_token(service: str = "ag") -> str | None:
    """Return the decrypted token for ``service``, or ``None``.

    Any read failure (missing file, corrupt JSON, undecryptable blob) degrades
    silently to ``None`` so callers fall through to the missing-token path.
    """

    try:
        raw = json.loads(token_path(service).read_text(encoding="utf-8"))
        if raw.get("version") != _SCHEMA_VERSION or raw.get("service") != service:
            return None
        secret = base64.b64decode(raw["secret"])
        if raw.get("encryption") == "dpapi" and _is_windows():
            return _win32_crypt(secret, protect=False).decode("utf-8")
        if raw.get("encryption") == "base64":
            return secret.decode("utf-8")
        return None
    except Exception:
        return None


def token_info(service: str = "ag") -> dict[str, Any] | None:
    """Return ``{encryption, created_at}`` metadata for the stored token, or ``None``."""

    try:
        raw = json.loads(token_path(service).read_text(encoding="utf-8"))
        return {
            "encryption": raw.get("encryption"),
            "created_at": raw.get("created_at"),
        }
    except Exception:
        return None


def clear_token(service: str = "ag") -> bool:
    """Delete ``service``'s stored token; return whether one existed."""

    path = token_path(service)
    if not path.is_file():
        return False
    with contextlib.suppress(OSError):
        path.unlink()
    return True


__all__ = ["clear_token", "read_token", "store_token", "token_info", "token_path"]
