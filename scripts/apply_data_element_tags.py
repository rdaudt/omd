#!/usr/bin/env python3
"""Apply DataElementType.* tags to columns by matching column names against namePatterns.

Reads definitions from taxonomies/data-element-type.yaml, fetches tables via the OMD API,
and patches matching columns. Columns that already carry any DataElementType.* tag are
skipped (safe to re-run).

Required environment variables:
  OMD_HOST       OpenMetadata API base URL, e.g. http://localhost:8585/api
  OMD_JWT_TOKEN  JWT token for an account with table tag management permissions

Dependencies:
  pip install "openmetadata-ingestion~=1.12.0" pyyaml
"""

from __future__ import annotations

import argparse
import copy
import os
import pathlib
import re
from dataclasses import dataclass, field

import yaml

from metadata.generated.schema.entity.data.table import Column as TableColumn, Table
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    AuthProvider,
    OpenMetadataConnection,
)
from metadata.generated.schema.security.client.openMetadataJWTClientConfig import (
    OpenMetadataJWTClientConfig,
)
from metadata.generated.schema.type.tagLabel import LabelType, State, TagLabel, TagSource
from metadata.ingestion.ometa.ometa_api import OpenMetadata

YAML_PATH = pathlib.Path(__file__).parent.parent / "taxonomies" / "data-element-type.yaml"
DATA_ELEMENT_PREFIX = "DataElementType."


# ---------------------------------------------------------------------------
# Data model (namePatterns subset of create_custom_classification.py TagSpec)
# ---------------------------------------------------------------------------


@dataclass
class TagSpec:
    fqn: str
    description: str
    name_patterns: list[str] = field(default_factory=list)
    children: list["TagSpec"] = field(default_factory=list)


def _parse_tag(raw: dict) -> TagSpec:
    spec = TagSpec(
        fqn=raw["name"],
        description=raw.get("description", ""),
        name_patterns=raw.get("namePatterns", []),
    )
    for child_raw in raw.get("childTags", []):
        spec.children.append(_parse_tag(child_raw))
    return spec


def load_taxonomy(path: pathlib.Path) -> list[TagSpec]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return [_parse_tag(t) for t in data.get("tags", [])]


# ---------------------------------------------------------------------------
# Pattern index: flat list of (TagSpec, compiled patterns), deepest-first
# ---------------------------------------------------------------------------


def _flatten(tags: list[TagSpec]) -> list[TagSpec]:
    result: list[TagSpec] = []
    for tag in tags:
        result.append(tag)
        result.extend(_flatten(tag.children))
    return result


def build_pattern_index(tags: list[TagSpec]) -> list[tuple[TagSpec, list[re.Pattern]]]:
    index = [
        (tag, [re.compile(p, re.IGNORECASE) for p in tag.name_patterns])
        for tag in _flatten(tags)
        if tag.name_patterns
    ]
    # deeper FQNs first so more-specific tags win over parent tags
    index.sort(key=lambda x: x[0].fqn.count("."), reverse=True)
    return index


# ---------------------------------------------------------------------------
# OMD client (same pattern as create_custom_classification.py)
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
# Schema / table filtering
# ---------------------------------------------------------------------------


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def filter_passes(schema: str, table: str, args: argparse.Namespace) -> bool:
    inc_s = _compile(args.schema_include)
    exc_s = _compile(args.schema_exclude)
    inc_t = _compile(args.table_include)
    exc_t = _compile(args.table_exclude)

    if inc_s and not any(p.search(schema) for p in inc_s):
        return False
    if exc_s and any(p.search(schema) for p in exc_s):
        return False
    if inc_t and not any(p.search(table) for p in inc_t):
        return False
    if exc_t and any(p.search(table) for p in exc_t):
        return False
    return True


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------


def _str(obj) -> str:
    """Extract string value from a pydantic root model or plain value."""
    if isinstance(obj, str):
        return obj
    # pydantic v1 root model (__root__) or pydantic v2 (.root)
    root = getattr(obj, "__root__", None)
    if root is not None:
        return str(root)
    root = getattr(obj, "root", None)
    if root is not None:
        return str(root)
    return str(obj)


def column_has_data_element_tag(column: TableColumn) -> bool:
    if not column.tags:
        return False
    return any(_str(tag.tagFQN).startswith(DATA_ELEMENT_PREFIX) for tag in column.tags)


def find_best_tag(
    column_name: str, pattern_index: list[tuple[TagSpec, list[re.Pattern]]]
) -> TagSpec | None:
    for tag, patterns in pattern_index:
        if any(p.search(column_name) for p in patterns):
            return tag
    return None


def make_tag_label(tag_fqn: str) -> TagLabel:
    return TagLabel(
        tagFQN=tag_fqn,
        labelType=LabelType.Automated,
        state=State.Confirmed,
        source=TagSource.Classification,
    )


# ---------------------------------------------------------------------------
# Apply staged assignments (one PATCH per table)
# ---------------------------------------------------------------------------


def apply_staged_tags(
    client: OpenMetadata,
    staged: dict[str, tuple[str, list[tuple[str, str]]]],
    dry_run: bool,
) -> None:
    """staged: {table_id: (display_label, [(col_name, tag_fqn), ...])}"""
    for table_id, (label, assignments) in staged.items():
        original = client.get_by_id(entity=Table, entity_id=table_id, fields=["columns"])
        if original is None:
            print(f"  [warn]    could not fetch table {table_id}")
            continue
        modified = copy.deepcopy(original)
        for col in modified.columns or []:
            col_name = _str(col.name)
            matches = [tag_fqn for name, tag_fqn in assignments if name == col_name]
            if not matches:
                continue
            if col.tags is None:
                col.tags = []
            for tag_fqn in matches:
                col.tags.append(make_tag_label(tag_fqn))
                verb = "[dry-run]" if dry_run else "[tag]    "
                print(f"  {verb} {label}.{col_name} → {tag_fqn}")
        if not dry_run:
            client.patch(entity=Table, source=original, destination=modified)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply DataElementType.* tags to columns based on column name patterns."
    )
    parser.add_argument("--service", required=True, help="OMD service name")
    parser.add_argument("--database", required=True, help="Database name within the service")
    parser.add_argument(
        "--schema-include", nargs="+", metavar="REGEX", default=[], dest="schema_include",
        help="Keep only schemas matching any of these regexes (case-insensitive)",
    )
    parser.add_argument(
        "--schema-exclude", nargs="+", metavar="REGEX", default=[], dest="schema_exclude",
        help="Drop schemas matching any of these regexes (applied after includes)",
    )
    parser.add_argument(
        "--table-include", nargs="+", metavar="REGEX", default=[], dest="table_include",
        help="Keep only tables matching any of these regexes (case-insensitive)",
    )
    parser.add_argument(
        "--table-exclude", nargs="+", metavar="REGEX", default=[], dest="table_exclude",
        help="Drop tables matching any of these regexes (applied after includes)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print what would change without writing anything",
    )
    args = parser.parse_args()

    all_tags = load_taxonomy(YAML_PATH)
    pattern_index = build_pattern_index(all_tags)
    print(f"Loaded {len(_flatten(all_tags))} tags, {len(pattern_index)} with name patterns.")

    client = build_client()
    # OMD /api/v1/tables "database" param expects the FQN: "service.database"
    db_fqn = f"{args.service}.{args.database}"
    tables = client.list_all_entities(
        entity=Table,
        fields=["columns", "fullyQualifiedName", "tags"],
        params={"database": db_fqn},
    )

    # staged: {table_id: (display_label, [(col_name, tag_fqn)])}
    staged: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    skip_count = 0
    tag_count = 0

    for table in tables:
        fqn = _str(table.fullyQualifiedName)
        parts = fqn.split(".")
        schema_name = parts[2] if len(parts) >= 4 else ""
        table_name = parts[3] if len(parts) >= 4 else ""

        if not filter_passes(schema_name, table_name, args):
            continue

        label = f"{schema_name}.{table_name}"
        table_id = _str(table.id)

        for column in table.columns or []:
            col_name = _str(column.name)
            if column_has_data_element_tag(column):
                print(f"  [skip]    {label}.{col_name}")
                skip_count += 1
                continue
            best = find_best_tag(col_name, pattern_index)
            if best:
                entry = staged.setdefault(table_id, (label, []))
                entry[1].append((col_name, best.fqn))
                tag_count += 1

    print(f"\nSummary: {skip_count} skipped, {tag_count} to tag across {len(staged)} tables.")

    if not staged:
        print("Nothing to do.")
        return

    apply_staged_tags(client, staged, args.dry_run)

    if args.dry_run:
        print("\nDry run complete — no changes written.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
