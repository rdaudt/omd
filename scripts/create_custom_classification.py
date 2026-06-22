#!/usr/bin/env python3
"""Create the DataElementType custom classification and tags in OpenMetadata.

Reads classification and tag definitions from taxonomies/data-element-type.yaml
and creates/updates them via the OpenMetadata SDK.

- Classification: skipped if it already exists (idempotent).
- Tags: always upserted so recognizer configs on existing tags are updated.

Required environment variables:
  OMD_HOST       OpenMetadata API base URL, e.g. http://localhost:8585/api
  OMD_JWT_TOKEN  JWT token for an account with classification/tag management permissions

Dependencies (install before running):
  pip install "openmetadata-ingestion~=1.12.0" pyyaml
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import yaml

from metadata.generated.schema.api.classification.createClassification import (
    AutoClassificationConfig,
    CreateClassificationRequest,
)
from metadata.generated.schema.api.classification.createTag import CreateTagRequest
from metadata.generated.schema.entity.classification.classification import Classification
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    AuthProvider,
    OpenMetadataConnection,
)
from metadata.generated.schema.security.client.openMetadataJWTClientConfig import (
    OpenMetadataJWTClientConfig,
)
from metadata.generated.schema.type.classificationLanguages import ClassificationLanguage
from metadata.generated.schema.type.patternRecognizer import PatternRecognizer
from metadata.generated.schema.type.predefinedRecognizer import Name as PredefinedName
from metadata.generated.schema.type.predefinedRecognizer import PredefinedRecognizer
from metadata.generated.schema.type.recognizer import Recognizer, RecognizerConfig, Target
from metadata.generated.schema.type.recognizers.patterns import Pattern
from metadata.generated.schema.type.recognizers.regexFlags import RegexFlags
from metadata.ingestion.ometa.ometa_api import OpenMetadata

YAML_PATH = pathlib.Path(__file__).parent.parent / "taxonomies" / "data-element-type.yaml"

_DEFAULT_REGEX_FLAGS = RegexFlags(ignoreCase=True, multiline=True, dotAll=True)


# ---------------------------------------------------------------------------
# Data model for the parsed taxonomy YAML
# ---------------------------------------------------------------------------


@dataclass
class TagSpec:
    fqn: str
    description: str
    display_name: str | None = None
    name_patterns: list[str] = field(default_factory=list)
    value_patterns: list[str] = field(default_factory=list)
    predefined_recognizer_names: list[str] = field(default_factory=list)
    auto_classification_enabled: bool = False
    auto_classification_priority: int = 50
    children: list["TagSpec"] = field(default_factory=list)

    @property
    def leaf_name(self) -> str:
        """Last dot-segment of the FQN — the value OMD stores as the tag name."""
        return self.fqn.rsplit(".", 1)[-1]

    @property
    def parent_fqn(self) -> str | None:
        """FQN of the parent tag, or None when this is a top-level tag."""
        prefix = self.fqn.rsplit(".", 1)[0]
        return prefix if "." in prefix else None


@dataclass
class ClassificationSpec:
    name: str
    display_name: str
    description: str
    mutually_exclusive: bool
    tags: list[TagSpec]


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def _parse_tag(raw: dict) -> TagSpec:
    spec = TagSpec(
        fqn=raw["name"],
        description=raw.get("description", ""),
        display_name=raw.get("displayName"),
        name_patterns=raw.get("namePatterns", []),
        value_patterns=raw.get("valuePatterns", []),
        predefined_recognizer_names=raw.get("predefinedRecognizers", []),
        auto_classification_enabled=raw.get("autoClassificationEnabled", False),
        auto_classification_priority=raw.get("autoClassificationPriority", 50),
    )
    for child_raw in raw.get("childTags", []):
        spec.children.append(_parse_tag(child_raw))
    return spec


def _count_tags(tags: list[TagSpec]) -> int:
    return sum(1 + _count_tags(t.children) for t in tags)


def load_yaml(path: pathlib.Path) -> ClassificationSpec:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    c = data["classification"]
    return ClassificationSpec(
        name=c["name"],
        display_name=c.get("displayName", c["name"]),
        description=c.get("description", ""),
        mutually_exclusive=c.get("mutuallyExclusive", False),
        tags=[_parse_tag(t) for t in data.get("tags", [])],
    )


# ---------------------------------------------------------------------------
# Recognizer construction
# ---------------------------------------------------------------------------


def _make_pattern_recognizer(
    patterns: list[str], score: float, target: Target, leaf_name: str, suffix: str
) -> Recognizer:
    sdk_patterns = [
        Pattern(name=f"{leaf_name}_pattern_{i}", regex=p, score=score)
        for i, p in enumerate(patterns)
    ]
    config = PatternRecognizer(
        type="pattern",
        patterns=sdk_patterns,
        regexFlags=_DEFAULT_REGEX_FLAGS,
        supportedLanguage=ClassificationLanguage.en,
    )
    return Recognizer(
        name=f"{leaf_name}{suffix}",
        enabled=True,
        target=target,
        confidenceThreshold=0.8,
        recognizerConfig=RecognizerConfig(root=config),
    )


def build_recognizers(tag: TagSpec) -> list[Recognizer]:
    recognizers: list[Recognizer] = []

    if tag.name_patterns:
        recognizers.append(
            _make_pattern_recognizer(
                tag.name_patterns, 0.85, Target.column_name, tag.leaf_name, "ColumnNameRecognizer"
            )
        )

    if tag.value_patterns:
        recognizers.append(
            _make_pattern_recognizer(
                tag.value_patterns, 0.90, Target.content, tag.leaf_name, "ValuePatternRecognizer"
            )
        )

    for predefined_name in tag.predefined_recognizer_names:
        config = PredefinedRecognizer(
            type="predefined",
            name=PredefinedName[predefined_name],
            supportedLanguage=ClassificationLanguage.en,
        )
        recognizers.append(
            Recognizer(
                name=f"{tag.leaf_name}{predefined_name}Recognizer",
                enabled=True,
                target=Target.content,
                confidenceThreshold=0.75,
                recognizerConfig=RecognizerConfig(root=config),
            )
        )

    return recognizers


# ---------------------------------------------------------------------------
# OpenMetadata client
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_client() -> OpenMetadata:
    server_config = OpenMetadataConnection(
        hostPort=_require_env("OMD_HOST"),
        authProvider=AuthProvider.openmetadata,
        securityConfig=OpenMetadataJWTClientConfig(jwtToken=_require_env("OMD_JWT_TOKEN")),
    )
    return OpenMetadata(server_config)


# ---------------------------------------------------------------------------
# Classification and tag helpers
# ---------------------------------------------------------------------------


def upsert_classification(
    client: OpenMetadata, spec: ClassificationSpec
) -> Classification:
    result = client.create_or_update(
        data=CreateClassificationRequest(
            name=spec.name,
            displayName=spec.display_name,
            description=spec.description,
            mutuallyExclusive=spec.mutually_exclusive,
            autoClassificationConfig=AutoClassificationConfig(
                enabled=True,
                minimumConfidence=0.8,
            ),
        )
    )
    print(f"  [upsert]  classification: {spec.name}  (autoClassificationConfig.enabled=True)")
    return result


def upsert_tag(client: OpenMetadata, classification_name: str, tag: TagSpec) -> None:
    recognizers = build_recognizers(tag) or None
    client.create_or_update(
        data=CreateTagRequest(
            name=tag.leaf_name,
            displayName=tag.display_name or tag.leaf_name,
            description=tag.description,
            classification=classification_name,
            parent=tag.parent_fqn,
            mutuallyExclusive=False,
            autoClassificationEnabled=tag.auto_classification_enabled,
            autoClassificationPriority=tag.auto_classification_priority,
            recognizers=recognizers,
        )
    )
    recognizer_count = len(recognizers) if recognizers else 0
    print(
        f"  [upsert]  tag: {tag.fqn}"
        f"  (autoClassify={tag.auto_classification_enabled}, recognizers={recognizer_count})"
    )

    for child in tag.children:
        upsert_tag(client, classification_name, child)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    spec = load_yaml(YAML_PATH)
    print(
        f"Loaded taxonomy '{spec.name}' from {YAML_PATH.name} "
        f"({_count_tags(spec.tags)} tags)"
    )

    client = build_client()

    upsert_classification(client, spec)

    for tag in spec.tags:
        upsert_tag(client, spec.name, tag)

    print("Done.")


if __name__ == "__main__":
    main()
