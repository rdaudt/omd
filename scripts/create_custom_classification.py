#!/usr/bin/env python3
"""Create the DataElementType custom classification and tags in OpenMetadata.

Reads classification and tag definitions from taxonomies/data-element-type.yaml
and creates them via the OpenMetadata SDK. Idempotent: existing classifications
and tags are skipped (logged and left unchanged).

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
    CreateClassificationRequest,
)
from metadata.generated.schema.api.classification.createTag import CreateTagRequest
from metadata.generated.schema.entity.classification.classification import Classification
from metadata.generated.schema.entity.classification.tag import Tag
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    AuthProvider,
    OpenMetadataConnection,
)
from metadata.generated.schema.security.client.openMetadataJWTClientConfig import (
    OpenMetadataJWTClientConfig,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata

YAML_PATH = pathlib.Path(__file__).parent.parent / "taxonomies" / "data-element-type.yaml"


# ---------------------------------------------------------------------------
# Data model for the parsed taxonomy YAML
# ---------------------------------------------------------------------------


@dataclass
class TagSpec:
    fqn: str
    description: str
    display_name: str | None = None
    children: list["TagSpec"] = field(default_factory=list)

    @property
    def leaf_name(self) -> str:
        """Last dot-segment of the FQN — the value OMD stores as the tag name."""
        return self.fqn.rsplit(".", 1)[-1]

    @property
    def parent_fqn(self) -> str | None:
        """FQN of the parent tag, or None when this is a top-level tag."""
        prefix = self.fqn.rsplit(".", 1)[0]
        # prefix has a dot only when there is a genuine parent tag between
        # the classification name and this tag (e.g. "DataElementType.Address")
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


def get_or_create_classification(
    client: OpenMetadata, spec: ClassificationSpec
) -> Classification:
    existing = client.get_by_name(entity=Classification, fqn=spec.name)
    if existing:
        print(f"  [skip]    classification already exists: {spec.name}")
        return existing

    result = client.create_or_update(
        data=CreateClassificationRequest(
            name=spec.name,
            displayName=spec.display_name,
            description=spec.description,
            mutuallyExclusive=spec.mutually_exclusive,
        )
    )
    print(f"  [created] classification: {spec.name}")
    return result


def get_or_create_tag(
    client: OpenMetadata, classification_name: str, tag: TagSpec
) -> None:
    existing = client.get_by_name(entity=Tag, fqn=tag.fqn)
    if existing:
        print(f"  [skip]    tag already exists: {tag.fqn}")
    else:
        client.create_or_update(
            data=CreateTagRequest(
                name=tag.leaf_name,
                displayName=tag.display_name or tag.leaf_name,
                description=tag.description,
                classification=classification_name,
                parent=tag.parent_fqn,   # None for top-level tags; FQN for child tags
                mutuallyExclusive=False,
            )
        )
        print(f"  [created] tag: {tag.fqn}")

    for child in tag.children:
        get_or_create_tag(client, classification_name, child)


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

    get_or_create_classification(client, spec)

    for tag in spec.tags:
        get_or_create_tag(client, spec.name, tag)

    print("Done.")


if __name__ == "__main__":
    main()
