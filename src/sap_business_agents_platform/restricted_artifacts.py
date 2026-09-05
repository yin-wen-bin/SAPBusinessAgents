from __future__ import annotations

import hashlib
import json
import os
import struct
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jsonschema import Draft202012Validator, FormatChecker

from .security import LocalSecretProtector


class RestrictedArtifactError(RuntimeError):
    def __init__(
        self, message: str, *, code: str = "restricted_artifact_error"
    ) -> None:
        super().__init__(message)
        self.code = code


class RestrictedArtifactStore:
    MAGIC = b"SAPBAR01"
    CHUNK_BYTES = 1024 * 1024
    MAX_PLAINTEXT_BYTES = 50 * 1024 * 1024

    def __init__(self, data_root: Path, store: Any, *, retention_days: int = 30) -> None:
        self.root = (data_root / "restricted-artifacts").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.protector = LocalSecretProtector(data_root)
        self.retention_days = max(1, retention_days)

    def materialize_skill_output(
        self,
        *,
        run_id: str,
        skill_id: str,
        output: dict[str, Any],
        skill_contract: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if skill_id == "sap-bank-receipt-evidence":
            return self._bank_receipt_projection(run_id, output)
        if skill_id == "sap-adt-table-export":
            rows = output.get("rows") if isinstance(output.get("rows"), list) else []
            if not rows:
                return dict(output), []
            artifact = self.create(
                run_id=run_id,
                rows=rows,
                classification="restricted-sap-raw-rows",
                hashed_indexes=[],
            )
            public = dict(output)
            public.pop("rows", None)
            public["rows_redacted"] = True
            public["returned_row_count"] = len(rows)
            public["restricted_artifact_ref"] = self.public_ref(artifact)
            source = public.get("source")
            if isinstance(source, dict):
                public["source"] = {
                    key: value
                    for key, value in source.items()
                    if key not in {"client", "endpoint", "metadata_endpoint", "system_alias"}
                }
            return public, [artifact]
        if (skill_contract or {}).get("restricted_projection_mode") == "declared_split":
            return self._declared_split_projection(run_id, output, skill_contract or {})
        if (skill_contract or {}).get("output_policy") == "restricted_artifact":
            raise RestrictedArtifactError(
                "Restricted Skill output has no trusted privacy projection.",
                code="restricted_projection_undeclared",
            )
        return dict(output), []

    def _declared_split_projection(
        self,
        run_id: str,
        output: dict[str, Any],
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        restricted_field = str(contract.get("restricted_rows_field") or "restricted_rows")
        artifact_field = str(contract.get("public_artifact_ref_field") or "restricted_artifact_ref")
        raw_rows = output.get(restricted_field)
        if not isinstance(raw_rows, list):
            raise RestrictedArtifactError(
                "Restricted Skill output does not contain its declared row array.",
                code="restricted_projection_invalid",
            )
        row_schema = contract.get("restricted_row_schema")
        public_schema = contract.get("public_output_schema")
        if not isinstance(row_schema, dict) or not isinstance(public_schema, dict):
            raise RestrictedArtifactError(
                "Restricted Skill projection schemas are unavailable.",
                code="restricted_projection_undeclared",
            )
        row_validator = Draft202012Validator(row_schema, format_checker=FormatChecker())
        rows: list[dict[str, Any]] = []
        for index, row in enumerate(raw_rows):
            if not isinstance(row, dict):
                raise RestrictedArtifactError(
                    f"Restricted row {index} is invalid.",
                    code="restricted_projection_invalid",
                )
            errors = sorted(row_validator.iter_errors(row), key=lambda item: list(item.path))
            if errors:
                raise RestrictedArtifactError(
                    "Restricted Skill row failed its trusted schema.",
                    code="restricted_projection_invalid",
                )
            rows.append(dict(row))
        key_fields = [str(item) for item in contract.get("restricted_business_key_fields") or []]
        if not key_fields or any(field not in row_schema.get("properties", {}) for field in key_fields):
            raise RestrictedArtifactError(
                "Restricted Skill business-key policy is invalid.",
                code="restricted_projection_undeclared",
            )
        indexes = [
            self.protector.hmac_descriptor(
                "\x1f".join(str(row.get(field) or "") for field in key_fields),
                domain=str(contract.get("restricted_index_domain") or "restricted-skill-row"),
            )
            for row in rows
        ]
        artifacts: list[dict[str, Any]] = []
        public = dict(output)
        public.pop(restricted_field, None)
        if rows:
            artifact = self.create(
                run_id=run_id,
                rows=rows,
                classification=str(
                    contract.get("restricted_classification")
                    or "restricted-sap-business-evidence"
                ),
                hashed_indexes=indexes,
            )
            public[artifact_field] = self.public_ref(artifact)
            artifacts.append(artifact)
        else:
            public[artifact_field] = None
        errors = sorted(
            Draft202012Validator(
                public_schema, format_checker=FormatChecker()
            ).iter_errors(public),
            key=lambda item: list(item.path),
        )
        if errors:
            for artifact in artifacts:
                self.delete(run_id, str(artifact["artifact_id"]), reason="public_projection_invalid")
            raise RestrictedArtifactError(
                "Public Skill projection failed its trusted schema.",
                code="restricted_public_projection_invalid",
            )
        return public, artifacts

    def _bank_receipt_projection(
        self, run_id: str, output: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw_receipts = output.get("receipts")
        receipts = raw_receipts if isinstance(raw_receipts, list) else []
        restricted_rows: list[dict[str, Any]] = []
        public_receipts: list[dict[str, Any]] = []
        profile = output.get("source_profile") if isinstance(output.get("source_profile"), dict) else {}
        account_key_id = str(profile.get("hash_key_id") or "sap-skill-account-hash")
        indexes: list[dict[str, str]] = []
        for item in receipts:
            if not isinstance(item, dict):
                continue
            statement_id = str(item.get("statement_id") or "")
            statement_item = str(item.get("statement_item") or "")
            business_key = f"{statement_id}\x1f{statement_item}"
            indexes.append(
                self.protector.hmac_descriptor(
                    business_key, domain="bank-receipt-artifact-row"
                )
            )
            restricted_rows.append(
                {
                    "statement_id": statement_id,
                    "statement_item": statement_item,
                    "payer_name": item.get("payer_name"),
                    "bank_reference": item.get("bank_reference"),
                }
            )
            safe = {
                key: value
                for key, value in item.items()
                if key not in {"payer_name", "bank_reference", "payer_account_hash"}
            }
            if item.get("payer_account_hash"):
                safe["payer_account_hash"] = {
                    "algorithm": "HMAC-SHA-256",
                    "key_id": account_key_id,
                    "domain": "payer-bank-account",
                    "digest": str(item["payer_account_hash"]),
                }
            public_receipts.append(safe)
        artifacts: list[dict[str, Any]] = []
        public = dict(output)
        public["receipts"] = public_receipts
        if restricted_rows:
            artifact = self.create(
                run_id=run_id,
                rows=restricted_rows,
                classification="restricted-bank-receipt-detail",
                hashed_indexes=indexes,
            )
            public["restricted_artifact_ref"] = self.public_ref(artifact)
            artifacts.append(artifact)
        return public, artifacts

    def create(
        self,
        *,
        run_id: str,
        rows: Iterable[dict[str, Any]],
        classification: str,
        hashed_indexes: list[dict[str, str]],
    ) -> dict[str, Any]:
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        run_root = (self.root / run_id).resolve()
        if self.root not in run_root.parents:
            raise RestrictedArtifactError("Restricted artifact path escaped its root.")
        run_root.mkdir(parents=True, exist_ok=True)
        final_path = run_root / f"{artifact_id}.aesgcm"
        temporary = run_root / f".{artifact_id}.{uuid.uuid4().hex}.tmp"
        data_key = os.urandom(32)
        aes = AESGCM(data_key)
        row_count = 0
        plaintext_bytes = 0
        plaintext_buffer = bytearray()
        try:
            with temporary.open("wb") as stream:
                stream.write(self.MAGIC)
                for row in rows:
                    encoded = json.dumps(
                        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8") + b"\n"
                    plaintext_bytes += len(encoded)
                    if plaintext_bytes > self.MAX_PLAINTEXT_BYTES:
                        raise RestrictedArtifactError(
                            "Restricted evidence exceeds the 50 MB limit.",
                            code="restricted_artifact_too_large",
                        )
                    row_count += 1
                    plaintext_buffer.extend(encoded)
                    while len(plaintext_buffer) >= self.CHUNK_BYTES:
                        chunk = bytes(plaintext_buffer[: self.CHUNK_BYTES])
                        del plaintext_buffer[: self.CHUNK_BYTES]
                        self._write_chunk(stream, aes, artifact_id, chunk)
                if plaintext_buffer:
                    self._write_chunk(stream, aes, artifact_id, bytes(plaintext_buffer))
                stream.flush()
                os.fsync(stream.fileno())
            digest = self._file_sha256(temporary)
            os.replace(temporary, final_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        created = datetime.now(timezone.utc)
        expires = created + timedelta(days=self.retention_days)
        wrapped_key = self.protector.protect(
            data_key, purpose=f"sapba-artifact-key:{run_id}:{artifact_id}"
        )
        try:
            self.store.create_structured_artifact(
                artifact_id=artifact_id,
                run_id=run_id,
                format="ndjson",
                row_count=row_count,
                byte_count=final_path.stat().st_size,
                sha256=digest,
                classification=classification,
                hashed_indexes=hashed_indexes,
                path=str(final_path),
                protected_key=wrapped_key,
                created_at=created.isoformat(),
                expires_at=expires.isoformat(),
            )
            return self.store.get_structured_artifact(run_id, artifact_id)
        except Exception:
            # A ciphertext without a matching database record cannot be reached or
            # expired by the application. Remove it if the atomic metadata commit
            # fails so restricted evidence never becomes an orphaned file.
            final_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def public_ref(artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            key: artifact.get(key)
            for key in (
                "artifact_id",
                "format",
                "row_count",
                "byte_count",
                "sha256",
                "classification",
                "hashed_indexes",
                "created_at",
                "expires_at",
                "deleted_at",
                "deletion_reason",
            )
        }

    def rows(self, run_id: str, artifact_id: str) -> list[dict[str, Any]]:
        artifact = self.store.get_structured_artifact(run_id, artifact_id)
        self._require_available(artifact)
        path = Path(str(artifact["path"])).resolve()
        expected_root = (self.root / run_id).resolve()
        if expected_root not in path.parents or not path.is_file():
            raise RestrictedArtifactError(
                "Restricted evidence file is unavailable.", code="artifact_unavailable"
            )
        if path.stat().st_size > self.MAX_PLAINTEXT_BYTES + 4096:
            raise RestrictedArtifactError(
                "Restricted evidence exceeds the supported size.",
                code="restricted_artifact_too_large",
            )
        cipher_digest = self._file_sha256(path)
        if cipher_digest != artifact["sha256"]:
            raise RestrictedArtifactError(
                "Restricted evidence integrity check failed.", code="artifact_digest_mismatch"
            )
        data_key = self.protector.unprotect(
            artifact["protected_key"],
            purpose=f"sapba-artifact-key:{run_id}:{artifact_id}",
        )
        plaintext = bytearray()
        with path.open("rb") as stream:
            if stream.read(len(self.MAGIC)) != self.MAGIC:
                raise RestrictedArtifactError(
                    "Restricted evidence format is invalid.", code="artifact_format_invalid"
                )
            aes = AESGCM(data_key)
            while True:
                length_raw = stream.read(4)
                if not length_raw:
                    break
                if len(length_raw) != 4:
                    raise RestrictedArtifactError("Restricted evidence chunk is truncated.")
                length = struct.unpack(">I", length_raw)[0]
                nonce = stream.read(12)
                ciphertext = stream.read(length)
                if len(nonce) != 12 or len(ciphertext) != length:
                    raise RestrictedArtifactError("Restricted evidence chunk is truncated.")
                plaintext.extend(aes.decrypt(nonce, ciphertext, artifact_id.encode("utf-8")))
                if len(plaintext) > self.MAX_PLAINTEXT_BYTES:
                    raise RestrictedArtifactError(
                        "Restricted evidence exceeds the 50 MB limit.",
                        code="restricted_artifact_too_large",
                    )
        result: list[dict[str, Any]] = []
        for line in bytes(plaintext).splitlines():
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, dict):
                raise RestrictedArtifactError("Restricted evidence row is invalid.")
            result.append(value)
        return result

    def delete(self, run_id: str, artifact_id: str, *, reason: str) -> dict[str, Any]:
        artifact = self.store.get_structured_artifact(run_id, artifact_id)
        path = Path(str(artifact.get("path") or ""))
        try:
            if path.is_file():
                path.unlink()
        finally:
            self.store.delete_structured_artifact(run_id, artifact_id, reason=reason)
        return self.store.get_structured_artifact(run_id, artifact_id)

    @staticmethod
    def _write_chunk(stream: Any, aes: AESGCM, artifact_id: str, chunk: bytes) -> None:
        nonce = os.urandom(12)
        ciphertext = aes.encrypt(nonce, chunk, artifact_id.encode("utf-8"))
        stream.write(struct.pack(">I", len(ciphertext)))
        stream.write(nonce)
        stream.write(ciphertext)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _require_available(artifact: dict[str, Any]) -> None:
        if artifact.get("deleted_at"):
            raise RestrictedArtifactError(
                "Restricted evidence was deleted.", code="artifact_deleted"
            )
        expires = datetime.fromisoformat(str(artifact["expires_at"]))
        if expires <= datetime.now(timezone.utc):
            raise RestrictedArtifactError(
                "Restricted evidence has expired.", code="artifact_expired"
            )
