"""Private strict codec and bounded local persistence for evidence artifacts."""

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_SCHEMA_NAME = "effectprobe.private.evidence"
_SCHEMA_VERSION = 1
_MAX_ARTIFACT_BYTES = 1_048_576
_MAX_DEPTH = 32
_MAX_CONTAINER_ITEMS = 256
_MAX_STRING_CHARS = 65_536
_MAX_TOTAL_NODES = 50_000
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "producer",
        "replay",
        "compatibility",
        "scope",
        "contracts",
        "outcomes",
        "evidence",
        "provenance",
        "redaction",
        "reproduction",
    }
)

type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class FrozenJsonObject:
    """Recursively immutable JSON object."""

    items: tuple[tuple[str, "FrozenJsonValue"], ...]


type FrozenJsonValue = None | bool | int | str | tuple[FrozenJsonValue, ...] | FrozenJsonObject


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    """One validated private evidence artifact."""

    root: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class ReadEvidenceArtifact:
    """Validated artifact plus the digest of its exact canonical file bytes."""

    artifact: EvidenceArtifact
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CompatibilityDifference:
    """One exact leaf mismatch between recorded and live compatibility."""

    path: str
    recorded: str
    current: str


class EvidenceArtifactError(RuntimeError):
    """Base error for private evidence persistence."""


class ArtifactFormatError(EvidenceArtifactError):
    """Artifact bytes do not satisfy the private schema and codec."""


class ArtifactWriteError(EvidenceArtifactError):
    """A bounded exclusive artifact write could not complete."""


class ArtifactCompatibilityError(EvidenceArtifactError):
    """Exact replay was refused because recorded and live descriptors differ."""

    def __init__(self, differences: tuple[CompatibilityDifference, ...]) -> None:
        self.differences = differences
        paths = ", ".join(item.path for item in differences)
        super().__init__(f"artifact compatibility mismatch: {paths}")


type ArtifactValidator = Callable[[dict[str, JsonValue]], None]


def _format_error(message: str) -> ArtifactFormatError:
    return ArtifactFormatError(message)


def _require_exact_fields(value: dict[str, JsonValue], expected: frozenset[str], path: str) -> None:
    fields = frozenset(value)
    missing = expected - fields
    unknown = fields - expected
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown {', '.join(sorted(unknown))}")
        raise _format_error(f"{path} fields are invalid: {'; '.join(details)}")


def require_object(value: JsonValue, path: str) -> dict[str, JsonValue]:
    """Require one JSON object for a private schema decoder."""

    if not isinstance(value, dict):
        raise _format_error(f"{path} must be an object")
    return value


def require_list(value: JsonValue, path: str) -> list[JsonValue]:
    """Require one bounded JSON array for a private schema decoder."""

    if not isinstance(value, list):
        raise _format_error(f"{path} must be an array")
    return value


def require_string(value: JsonValue, path: str) -> str:
    """Require one JSON string for a private schema decoder."""

    if not isinstance(value, str):
        raise _format_error(f"{path} must be a string")
    return value


def require_bool(value: JsonValue, path: str) -> bool:
    """Require one JSON boolean for a private schema decoder."""

    if not isinstance(value, bool):
        raise _format_error(f"{path} must be a boolean")
    return value


def require_int(value: JsonValue, path: str) -> int:
    """Require one JSON integer while rejecting booleans."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise _format_error(f"{path} must be an integer")
    return value


def require_nullable_string(value: JsonValue, path: str) -> str | None:
    """Require a string or null."""

    if value is None:
        return None
    return require_string(value, path)


def require_fields(value: dict[str, JsonValue], expected: frozenset[str], path: str) -> None:
    """Require exact object fields for a private schema decoder."""

    _require_exact_fields(value, expected, path)


def _freeze(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, dict):
        return FrozenJsonObject(tuple((key, _freeze(item)) for key, item in sorted(value.items())))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, FrozenJsonObject):
        return {key: _thaw(item) for key, item in value.items}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def artifact_payload(artifact: EvidenceArtifact) -> dict[str, JsonValue]:
    """Return a detached mutable JSON representation of a frozen artifact."""

    return cast("dict[str, JsonValue]", _thaw(artifact.root))


def _reject_constant(value: str) -> JsonValue:
    raise _format_error(f"non-finite JSON number is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _format_error(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _validate_limits(value: JsonValue, *, path: str, depth: int, nodes: list[int]) -> None:
    nodes[0] += 1
    if nodes[0] > _MAX_TOTAL_NODES:
        raise _format_error("artifact contains too many JSON values")
    if depth > _MAX_DEPTH:
        raise _format_error(f"{path} exceeds the maximum nesting depth")
    if isinstance(value, str):
        if len(value) > _MAX_STRING_CHARS:
            raise _format_error(f"{path} string is too long")
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if not -(2**63) <= value < 2**63:
            raise _format_error(f"{path} integer is outside the signed 64-bit range")
        return
    if isinstance(value, list):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise _format_error(f"{path} array contains too many items")
        for index, item in enumerate(value):
            _validate_limits(item, path=f"{path}[{index}]", depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise _format_error(f"{path} object contains too many fields")
        for key, item in value.items():
            if len(key) > 256:
                raise _format_error(f"{path} contains an overlong field name")
            _validate_limits(item, path=f"{path}.{key}", depth=depth + 1, nodes=nodes)


def _validate_envelope(payload: dict[str, JsonValue]) -> None:
    _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "artifact")
    schema = require_object(payload["schema"], "artifact.schema")
    _require_exact_fields(schema, frozenset({"name", "version"}), "artifact.schema")
    if require_string(schema["name"], "artifact.schema.name") != _SCHEMA_NAME:
        raise _format_error("unsupported artifact schema name")
    if require_int(schema["version"], "artifact.schema.version") != _SCHEMA_VERSION:
        raise _format_error("unsupported artifact schema version")
    for field in _TOP_LEVEL_FIELDS - {"schema", "reproduction"}:
        require_object(payload[field], f"artifact.{field}")
    if payload["reproduction"] is not None:
        require_object(payload["reproduction"], "artifact.reproduction")


def evidence_artifact_from_payload(
    payload: dict[str, JsonValue], *, validator: ArtifactValidator | None = None
) -> EvidenceArtifact:
    """Validate and freeze an in-memory private artifact payload."""

    _validate_limits(payload, path="artifact", depth=0, nodes=[0])
    _validate_envelope(payload)
    if validator is not None:
        validator(payload)
    return EvidenceArtifact(cast("FrozenJsonObject", _freeze(payload)))


def canonical_artifact_bytes(artifact: EvidenceArtifact) -> bytes:
    """Encode one validated artifact in the only accepted canonical form."""

    payload = artifact_payload(artifact)
    try:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _format_error(f"artifact cannot be encoded canonically: {error}") from error
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise _format_error("artifact exceeds the 1 MiB encoded-size limit")
    return encoded


def artifact_sha256(artifact: EvidenceArtifact) -> str:
    """Hash the canonical bytes of an in-memory artifact."""

    return hashlib.sha256(canonical_artifact_bytes(artifact)).hexdigest()


def _parse_artifact_bytes(data: bytes, validator: ArtifactValidator | None) -> EvidenceArtifact:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _format_error("artifact is not valid UTF-8") from error
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ArtifactFormatError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise _format_error(f"artifact is not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise _format_error("artifact root must be an object")
    artifact = evidence_artifact_from_payload(
        cast("dict[str, JsonValue]", decoded), validator=validator
    )
    if canonical_artifact_bytes(artifact) != data:
        raise _format_error("artifact bytes are not in canonical form")
    return artifact


def read_evidence_artifact(
    path: Path, *, validator: ArtifactValidator | None = None
) -> ReadEvidenceArtifact:
    """Read one bounded regular file without following a symlink."""

    try:
        before = path.lstat()
    except OSError as error:
        raise ArtifactFormatError(
            f"artifact cannot be inspected: {type(error).__name__}"
        ) from error
    if not stat.S_ISREG(before.st_mode):
        raise _format_error("artifact path must name a regular non-symlink file")
    if before.st_size > _MAX_ARTIFACT_BYTES:
        raise _format_error("artifact exceeds the 1 MiB file-size limit")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactFormatError(f"artifact cannot be opened: {type(error).__name__}") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _format_error("artifact changed while it was being opened")
        chunks: list[bytes] = []
        remaining = _MAX_ARTIFACT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise _format_error("artifact grew beyond the 1 MiB file-size limit")
    artifact = _parse_artifact_bytes(data, validator)
    return ReadEvidenceArtifact(
        artifact=artifact,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def write_evidence_artifact(path: Path, artifact: EvidenceArtifact) -> ReadEvidenceArtifact:
    """Publish canonical bytes exclusively through a same-directory hard link."""

    data = canonical_artifact_bytes(artifact)
    parent = path.parent
    try:
        parent_status = parent.lstat()
    except OSError as error:
        raise ArtifactWriteError(
            f"artifact parent cannot be inspected: {type(error).__name__}"
        ) from error
    if not stat.S_ISDIR(parent_status.st_mode):
        raise ArtifactWriteError("artifact parent must be an existing non-symlink directory")

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ArtifactWriteError("artifact destination already exists") from error
        except OSError as error:
            raise ArtifactWriteError(
                f"artifact could not be published: {type(error).__name__}"
            ) from error
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except ArtifactWriteError:
        raise
    except OSError as error:
        raise ArtifactWriteError(f"artifact write failed: {type(error).__name__}") from error
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
    return ReadEvidenceArtifact(
        artifact=artifact,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


class _Missing:
    pass


type ComparableJsonValue = JsonValue | _Missing


def _safe_summary(value: ComparableJsonValue) -> str:
    if value is _MISSING:
        return "<missing>"
    if value is None or isinstance(value, bool | int):
        return str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, str):
        return value if len(value) <= 80 else f"{value[:77]}..."
    if isinstance(value, list):
        return f"array[{len(cast('list[JsonValue]', value))}]"
    if isinstance(value, dict):
        return f"object[{len(cast('dict[str, JsonValue]', value))}]"
    return "<unknown>"


_MISSING = _Missing()


def compatibility_differences(
    recorded: dict[str, JsonValue], current: dict[str, JsonValue]
) -> tuple[CompatibilityDifference, ...]:
    """Return every exact compatibility mismatch in deterministic path order."""

    differences: list[CompatibilityDifference] = []

    def compare(left: ComparableJsonValue, right: ComparableJsonValue, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            left_object = cast("dict[str, JsonValue]", left)
            right_object = cast("dict[str, JsonValue]", right)
            for key in sorted(left_object.keys() | right_object.keys()):
                compare(
                    left_object.get(key, _MISSING),
                    right_object.get(key, _MISSING),
                    f"{path}.{key}",
                )
            return
        if isinstance(left, list) and isinstance(right, list):
            left_list = cast("list[JsonValue]", left)
            right_list = cast("list[JsonValue]", right)
            for index in range(max(len(left_list), len(right_list))):
                left_item: ComparableJsonValue = (
                    left_list[index] if index < len(left_list) else _MISSING
                )
                right_item: ComparableJsonValue = (
                    right_list[index] if index < len(right_list) else _MISSING
                )
                compare(left_item, right_item, f"{path}[{index}]")
            return
        if type(left) is not type(right) or left != right:
            differences.append(
                CompatibilityDifference(
                    path=path,
                    recorded=_safe_summary(left),
                    current=_safe_summary(right),
                )
            )

    compare(recorded, current, "compatibility")
    return tuple(differences)


def require_compatible(recorded: dict[str, JsonValue], current: dict[str, JsonValue]) -> None:
    """Refuse exact replay on any compatibility descriptor drift."""

    differences = compatibility_differences(recorded, current)
    if differences:
        raise ArtifactCompatibilityError(differences)
