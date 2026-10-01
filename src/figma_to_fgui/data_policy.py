"""Shared deterministic JSON and public-data policy for project-neutral contracts."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_SECRET_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "figmaaccesstoken",
        "password",
        "refreshtoken",
        "secret",
        "token",
    }
)
_RAW_PAYLOAD_KEYS = frozenset(
    {
        "base64",
        "base64data",
        "binary",
        "binarydata",
        "blob",
        "bytes",
        "filebytes",
        "imagebytes",
        "payloadbytes",
        "rawbytes",
    }
)
_TARGET_BINDING_KEYS = frozenset(
    {
        "bindingcomponentid",
        "bindingcomponent",
        "bindingpackageid",
        "bindingpackage",
        "componentid",
        "fguicomponent",
        "fairyguicomponentid",
        "fairyguicomponent",
        "fairyguipackageid",
        "fairyguipackage",
        "fguicomponentid",
        "fguipackageid",
        "fguipackage",
        "packageid",
        "packageitemid",
        "packagekey",
        "packageref",
        "pkg",
        "src",
        "targetcomponentid",
        "targetcomponent",
        "targetpackageid",
        "targetpackage",
        "targetpkg",
        "targetsrc",
    }
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_/\\])[A-Za-z]:[\\/]\S*")
_UNC_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_/\\])(?:\\\\|//)\S+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s=(:,;\[{'\"])/(?!/)\S+")

_TARGET_BINDING_MARKERS = tuple(
    key for key in _TARGET_BINDING_KEYS if key not in {"pkg", "src"}
)
_SECRET_MARKERS = (
    "accesstoken",
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "credential",
    "figmaaccesstoken",
    "password",
    "refreshtoken",
)
_RAW_PAYLOAD_MARKERS = (
    "base64",
    "binarydata",
    "filebytes",
    "imagebytes",
    "payloadbytes",
    "rawbytes",
)
_PUBLIC_TOKEN_PREFIXES = (
    "color",
    "design",
    "semantic",
    "spacing",
    "style",
    "typography",
)
MAX_OPAQUE_DATA_DEPTH = 256


class FrozenDict(dict[str, Any]):
    """A dict-compatible mapping that rejects ordinary mutation methods."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("mapping is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable  # type: ignore[assignment]


@dataclass(frozen=True)
class DataViolation:
    category: str
    path: str


def freeze_mapping(value: Mapping[str, Any]) -> FrozenDict:
    """Copy and freeze a mapping whose values already have typed model contracts."""
    return FrozenDict({key: value[key] for key in sorted(value)})


def normalized_key(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _key_category(normalized: str) -> str | None:
    if normalized in _TARGET_BINDING_KEYS or any(
        marker in normalized for marker in _TARGET_BINDING_MARKERS
    ):
        return "target_binding"
    generic_secret_field = "secret" in normalized or (
        "token" in normalized
        and not normalized.startswith(_PUBLIC_TOKEN_PREFIXES)
    )
    if (
        normalized in _SECRET_KEYS
        or generic_secret_field
        or any(marker in normalized for marker in _SECRET_MARKERS)
    ):
        return "secret"
    if (
        normalized in _RAW_PAYLOAD_KEYS
        or normalized.endswith(("blob", "bytes"))
        or any(marker in normalized for marker in _RAW_PAYLOAD_MARKERS)
    ):
        return "raw_payload"
    return None


def _stable_sort_key(value: Any) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"


def freeze_json_value(value: Any, *, _depth: int = 0) -> Any:
    """Copy and freeze a deterministic JSON-compatible value.

    Sets are accepted only as an input convenience and become stably ordered tuples.
    Raw bytes, arbitrary objects, non-string mapping keys, and non-finite floats are
    rejected at the schema boundary so callers receive a normal validation error.
    """
    if _depth > MAX_OPAQUE_DATA_DEPTH:
        raise ValueError("opaque JSON facts exceed the supported nesting depth")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("opaque JSON facts require finite numbers")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("opaque JSON facts cannot contain raw bytes")  # noqa: TRY004
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("opaque JSON fact mappings require string keys")
        return FrozenDict(
            {
                key: freeze_json_value(value[key], _depth=_depth + 1)
                for key in sorted(value)
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(item, _depth=_depth + 1) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen = (freeze_json_value(item, _depth=_depth + 1) for item in value)
        return tuple(sorted(frozen, key=_stable_sort_key))
    raise ValueError("opaque facts must contain only deterministic JSON values")


def _is_absolute_local_path(value: str) -> bool:
    return (
        bool(_WINDOWS_ABSOLUTE_PATH.search(value))
        or bool(_UNC_ABSOLUTE_PATH.search(value))
        or bool(_POSIX_ABSOLUTE_PATH.search(value))
        or "file://" in value.casefold()
    )


def _redacted_mapping_key(value: object) -> str:
    raw = str(value)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"<redacted-key:{digest}>"


def private_data_violations(
    value: Any,
    path: str = "$",
    *,
    detect_absolute_paths: bool = True,
) -> tuple[DataViolation, ...]:
    """Return safe JSON paths for private/raw/binding data without echoing values."""
    violations: list[DataViolation] = []
    pending: list[tuple[Any, str, int]] = [(value, path, 0)]
    while pending:
        current, current_path, depth = pending.pop()
        if depth > MAX_OPAQUE_DATA_DEPTH:
            violations.append(DataViolation("opaque_depth", current_path))
            continue
        if isinstance(current, Mapping):
            nested_values: list[tuple[Any, str, int]] = []
            for index, key in enumerate(sorted(current, key=str)):
                nested_path = f"{current_path}.{key}"
                if isinstance(key, str) and _is_absolute_local_path(key):
                    violations.append(
                        DataViolation(
                            "absolute_path",
                            f"{current_path}.<private-key:{index}>",
                        )
                    )
                    continue
                category = _key_category(normalized_key(key))
                if category is not None:
                    violations.append(DataViolation(category, nested_path))
                    continue
                nested_values.append((current[key], nested_path, depth + 1))
            pending.extend(reversed(nested_values))
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            pending.extend(
                (nested, f"{current_path}[{index}]", depth + 1)
                for index, nested in reversed(tuple(enumerate(current)))
            )
        elif isinstance(current, (bytes, bytearray, memoryview)):
            violations.append(DataViolation("raw_payload", current_path))
        elif (
            detect_absolute_paths
            and isinstance(current, str)
            and _is_absolute_local_path(current)
        ):
            violations.append(DataViolation("absolute_path", current_path))
        elif isinstance(current, float) and not math.isfinite(current):
            violations.append(DataViolation("invalid_number", current_path))
        elif current is not None and not isinstance(
            current, (str, bool, int, float)
        ):
            violations.append(DataViolation("non_json", current_path))
    return tuple(violations)


def redact_private_data(
    value: Any,
    *,
    redact_absolute_paths: bool = True,
    _depth: int = 0,
) -> Any:
    """Return a JSON-safe public representation suitable for canonical hashing."""
    if _depth > MAX_OPAQUE_DATA_DEPTH:
        return "<redacted-depth>"
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key in sorted(value, key=str):
            output_key = (
                _redacted_mapping_key(key)
                if isinstance(key, str) and _is_absolute_local_path(key)
                else str(key)
            )
            while output_key in redacted:
                output_key += "_"
            normalized = normalized_key(key)
            if _key_category(normalized) is not None:
                redacted[output_key] = "<redacted>"
            else:
                redacted[output_key] = redact_private_data(
                    value[key],
                    redact_absolute_paths=redact_absolute_paths,
                    _depth=_depth + 1,
                )
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            redact_private_data(
                item,
                redact_absolute_paths=redact_absolute_paths,
                _depth=_depth + 1,
            )
            for item in value
        ]
    if isinstance(value, (set, frozenset)):
        return [
            redact_private_data(
                item,
                redact_absolute_paths=redact_absolute_paths,
                _depth=_depth + 1,
            )
            for item in sorted(value, key=_stable_sort_key)
        ]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<redacted>"
    if isinstance(value, str):
        return (
            "<redacted>"
            if redact_absolute_paths and _is_absolute_local_path(value)
            else value
        )
    if isinstance(value, float) and not math.isfinite(value):
        return "<invalid-number>"
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return "<non-json>"
