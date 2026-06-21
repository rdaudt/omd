# OpenMetadata 1.12.6 auto-classification notes

## Scope

These notes summarize how OpenMetadata's auto-classification workflow works in the 1.12.x documentation stream, with emphasis on what can be extended for a local Docker OpenMetadata 1.12.6 deployment connected to AdventureWorks on SQL Server.

## How auto-classification works

OpenMetadata treats auto-classification as a database-service workflow, separate from metadata ingestion. After tables and columns have been ingested, an Auto Classification agent/workflow can inspect column metadata and optionally sample rows, then apply or suggest governance tags.

The 1.12.x documentation describes two complementary detection paths:

1. **Column-name scanning**: column names are checked against regex-like rules for common sensitive-field patterns such as email, names, SSNs, bank accounts, and similar fields.
2. **Entity recognition over sample data**: if sample-data ingestion/storage is enabled, the workflow scans actual sample values with an NLP/NER engine. This helps classify ambiguous column names when values reveal sensitive data.

The default target taxonomy is PII. The usual outputs are `PII.Sensitive` and `PII.NonSensitive`; already-PII-tagged columns are skipped by the workflow.

## Workflow configuration knobs

The documented `AutoClassification` pipeline configuration includes these high-value controls:

| Setting | Purpose | Default in docs |
| --- | --- | --- |
| `enableAutoClassification` | Enables automatic sensitive-column detection and tagging. | `false` |
| `storeSampleData` | Stores sample rows for each table so the workflow can classify by values, not only names. | `true` |
| `sampleDataCount` | Number of sample rows to ingest when sample data is stored. | `50` |
| `confidence` | Minimum 0-100 score required to tag a column as sensitive from entity recognition. Higher values reduce false positives; lower values catch more. | `80` |
| `databaseFilterPattern`, `schemaFilterPattern`, `tableFilterPattern` | Scope the workflow to subsets of the service. | none |
| `classificationFilterPattern` | Scope the run to tables that already carry selected tags, tiers, or glossary patterns. | none |
| `useFqnForFiltering` | Match filters against FQNs rather than raw names. | `false` |
| `includeViews` | Include database views in classification. | `true` |

External execution uses the ingestion package with the PII processor extra:

```bash
pip install "openmetadata-ingestion[pii-processor]"
metadata classify -c <path-to-yaml>
```

The docs state that the Auto Classification workflow uses the `orm-profiler` processor, and should be run after metadata ingestion for the same `serviceName` so the ingestion bot can retrieve the service connection details from OpenMetadata.

## API surface relevant to extension

OpenMetadata exposes classification and tag resources through the REST API and Python SDK. The classification API can list and create/update classifications:

- `GET /api/v1/classifications` lists classification objects; by default it returns basic fields such as `id`, `name`, `fullyQualifiedName`, `provider`, and `mutuallyExclusive`.
- `POST /api/v1/classifications` creates a classification.
- `PUT /api/v1/classifications` performs an upsert using the same body shape as create.

These APIs are useful for creating your own taxonomy or preparing tags before running classification. However, the 1.12.x docs describe **tag mapping** as backend-configured and not available in the UI. In other words, tag creation and tag application are API-supported, but changing the built-in auto-classification recognizers/mappings is not presented as a normal UI operation in 1.12.x.

## Important constraints and caveats

- **PII-first behavior**: the 1.12.x Auto PII Tagging guide says the feature primarily applies the PII classification as Sensitive or Non-Sensitive.
- **Open-source vs Collate capability drift**: the same guide notes that a broader `General` classification (for tags such as Address or Name) was not available in OpenMetadata at that documentation point and was expected in the open-source release starting from 1.7.1. For a 1.12.6 install, verify the actual tags and fields in your local `/api/v1/tags` and `/api/v1/classifications` responses before assuming a broader taxonomy is active.
- **Sample-data dependency**: value-based detection requires sample data. Without `storeSampleData`, classification is limited to column-name scanning.
- **Model download dependency**: the PII processor may try to download a spaCy model. The docs call out certificate failures against GitHub-hosted spaCy model assets and recommend preinstalling `en_core_web_md-3.5.0` in the ingestion container if necessary.
- **Filtering footgun**: if `classificationFilterPattern` is supplied as a fully-qualified tag while `useFqnForFiltering` remains `false`, the run can match nothing and classify zero records.

## Practical extension paths for this repo

For AdventureWorks exploration, start with non-invasive extension artifacts rather than patching OpenMetadata itself:

1. **Inventory current classifications and tags** from the local server:
   - `GET http://localhost:<port>/api/v1/classifications`
   - `GET http://localhost:<port>/api/v1/tags?limit=1000`
2. **Run a baseline Auto Classification workflow** against the SQL Server service with `enableAutoClassification: true`, `storeSampleData: true`, and a conservative `confidence` such as `80` or `90`.
3. **Compare results against expected AdventureWorks columns**, for example `Person.EmailAddress.EmailAddress`, `Person.Person.FirstName`, `Person.Person.LastName`, phone-number columns, address fields, credit-card fields, and password/hash fields.
4. **Add a project-level expected-classification manifest** that maps table/column FQNs to intended tags and records whether each should be detected by column-name scanning, sample-value NER, or custom logic.
5. **Prototype an external post-processor** if built-in detection is not enough: read table/column metadata and sample data through OpenMetadata APIs, run custom recognizers locally, then apply tags back through the OpenMetadata API. This avoids modifying the OpenMetadata server until the desired recognizer behavior is proven.
6. **Only then consider OpenMetadata code changes or plugin work**, especially if you need first-class custom recognizers rather than a sidecar classifier.

## Source references

- OpenMetadata 1.12.x Auto-Classification overview: https://docs.open-metadata.org/v1.12.x/how-to-guides/data-governance/classification/auto-classification
- OpenMetadata 1.12.x External Auto Classification workflow: https://docs.open-metadata.org/v1.12.x/how-to-guides/data-governance/classification/auto-classification/external-workflow
- OpenMetadata 1.12.x Auto PII Tagging guide: https://docs.open-metadata.org/v1.12.x/how-to-guides/data-governance/classification/auto-classification/auto-pii-tagging
- OpenMetadata 1.12.x Classification list API: https://docs.open-metadata.org/v1.12.x/api-reference/governance/classifications/list
- OpenMetadata 1.12.x Classification create/upsert API: https://docs.open-metadata.org/v1.12.x/api-reference/governance/classifications/create
