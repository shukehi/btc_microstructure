"""Offline YAML loading, local path validation and canonical config digest."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from collections.abc import Callable
from typing import Any, cast

import yaml
from yaml.nodes import MappingNode, Node

from .config_models import ResearchConfig


class ConfigError(ValueError):
    """Invalid YAML or local configuration resource."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently overwriting them."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        # PyYAML stubs omit construct_object types; constrain this boundary explicitly.
        construct = cast(Callable[[Node, bool], object], self.construct_object)
        for key_node, value_node in node.value:
            key = construct(key_node, deep)
            try:
                if key in mapping:
                    raise ConfigError(f"YAML line {key_node.start_mark.line + 1}: duplicate key")
                mapping[key] = construct(value_node, deep)
            except TypeError as exc:
                raise ConfigError("YAML mapping keys must be scalar") from exc
        return mapping


def validate_paths(config: ResearchConfig, base_dir: Path) -> None:
    """Relative paths use the config file's directory; never create directories here."""
    paths = [("storage.root", config.storage.root, False)]
    if config.collector.endpoint_budget_file is not None:
        paths.append(
            ("collector.endpoint_budget_file", config.collector.endpoint_budget_file, True)
        )
    for field, raw, must_exist in paths:
        if "\0" in raw or raw.strip() != raw or raw.startswith("~") or "://" in raw:
            raise ConfigError(f"{field}: invalid local path (no expansion or URLs)")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        try:
            if must_exist:
                if not candidate.is_file():
                    raise ConfigError(f"{field}: must reference an existing file")
            else:
                for ancestor in (candidate, *candidate.parents):
                    if ancestor.exists() and not ancestor.is_dir():
                        raise ConfigError(f"{field}: directory path crosses an existing file")
        except (OSError, ValueError) as exc:
            raise ConfigError(f"{field}: inaccessible or invalid local path") from exc


def load_config(path: str | Path) -> ResearchConfig:
    source = Path(path)
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except ConfigError:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError(f"config: invalid YAML{location}") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError("config: cannot read UTF-8 configuration file") from exc
    config = ResearchConfig.model_validate(raw)
    validate_paths(config, source.parent)
    return config


def canonicalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Avoid normalize(): it can round under the process decimal context.
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {key: canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


def config_digest(config: ResearchConfig) -> str:
    payload = json.dumps(
        canonicalize(config.model_dump()),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
