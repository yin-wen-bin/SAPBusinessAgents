from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import secrets
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any


class SensitiveDataError(RuntimeError):
    def __init__(self, message: str, *, code: str = "sensitive_data_error") -> None:
        super().__init__(message)
        self.code = code


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi(value: bytes, *, protect: bool, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise SensitiveDataError(
            "Windows DPAPI is required for local sensitive-data protection.",
            code="dpapi_unavailable",
        )
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(value)
    entropy_blob, entropy_buffer = _blob(entropy)
    output_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    # Keep ctypes-owned input buffers alive through the native call.
    del input_buffer, entropy_buffer
    if not ok:
        raise SensitiveDataError(
            "Windows DPAPI could not protect the sensitive value.",
            code="dpapi_operation_failed",
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


class LocalSecretProtector:
    """Windows-user-bound protection and domain-separated HMAC identities."""

    BUSINESS_REFERENCE_KEY_ID = "sapba-business-reference-v1"

    def __init__(self, data_root: Path) -> None:
        self.root = (data_root / "security").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def protect(value: bytes, *, purpose: str) -> bytes:
        return _dpapi(value, protect=True, entropy=purpose.encode("utf-8"))

    @staticmethod
    def unprotect(value: bytes, *, purpose: str) -> bytes:
        return _dpapi(value, protect=False, entropy=purpose.encode("utf-8"))

    def create_secret_ref(
        self,
        *,
        run_id: str,
        field: str,
        value: str,
        domain: str,
    ) -> tuple[str, bytes, dict[str, str]]:
        secret_ref = f"secret_{uuid.uuid4().hex}"
        protected = self.protect(
            value.encode("utf-8"), purpose=f"sapba-run-secret:{run_id}:{field}"
        )
        return secret_ref, protected, self.hmac_descriptor(value, domain=domain)

    def reveal_run_secret(
        self, *, run_id: str, field: str, protected_value: bytes
    ) -> str:
        try:
            return self.unprotect(
                protected_value, purpose=f"sapba-run-secret:{run_id}:{field}"
            ).decode("utf-8")
        except (UnicodeDecodeError, SensitiveDataError) as exc:
            raise SensitiveDataError(
                "The sensitive run input could not be resolved.",
                code="secret_resolution_failed",
            ) from exc

    def hmac_descriptor(self, value: str, *, domain: str) -> dict[str, str]:
        key = self._load_or_create_hmac_key(self.BUSINESS_REFERENCE_KEY_ID)
        digest = hmac.new(
            key,
            domain.encode("utf-8") + b"\x00" + value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "algorithm": "HMAC-SHA-256",
            "key_id": self.BUSINESS_REFERENCE_KEY_ID,
            "domain": domain,
            "digest": digest,
        }

    def _load_or_create_hmac_key(self, key_id: str) -> bytes:
        path = self.root / f"{key_id}.key.dpapi"
        purpose = f"sapba-hmac-key:{key_id}"
        if path.is_file():
            return self.unprotect(path.read_bytes(), purpose=purpose)
        key = secrets.token_bytes(32)
        protected = self.protect(key, purpose=purpose)
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, path)
        return key


def sensitive_input_properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): definition
        for name, definition in properties.items()
        if isinstance(definition, dict) and definition.get("x-sapba-sensitive") is True
    }


def secret_domain(definition: dict[str, Any], field: str) -> str:
    kind = str(definition.get("x-sapba-secret-kind") or "business_reference")
    if kind == "business_reference" and field == "receipt_reference":
        return "bank-receipt-reference"
    return f"{kind}:{field}"
