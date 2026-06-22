# OMD Tooling Runbook

Operational reference for running scripts against the local OpenMetadata instance.

## Environment

| Component | Detail |
|---|---|
| OpenMetadata | `http://localhost:8585` — runs in Docker (local only) |
| Python venv | `D:\omd\.venv` — Python 3.10, created once (see Setup) |
| Taxonomy source | `taxonomies\data-element-type.yaml` |
| Scripts | `scripts\` |

---

## One-time Setup

### Start OpenMetadata

```powershell
# From D:\omd — docker-compose.yml is local only, not in the repo
docker compose up -d
```

Wait ~60 seconds for all containers to be healthy before running any scripts.

### Create the Python virtual environment

Only needed once, or after deleting `.venv`.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip "openmetadata-ingestion~=1.12.0" pyyaml
```

> If a file-lock error appears during install (common when VS Code is open), close VS Code,
> delete `.venv`, and retry.

---

## Activating the Virtual Environment

Activate the venv once per terminal session before running any script or `pip` command:

```powershell
.venv\Scripts\Activate.ps1
```

Your prompt will change to show `(.venv)`. To deactivate, run `deactivate`.

> **Execution policy error?** Run this once in an elevated PowerShell:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## Getting a JWT Token

Scripts authenticate as the **ingestion-bot** service account.

1. Open `http://localhost:8585` and log in as admin.
2. Go to **Settings → Bots → ingestion-bot**.
3. Copy the JWT token displayed on that page.

Set it in your shell before running any script:

```powershell
$env:OMD_HOST      = "http://localhost:8585/api"
$env:OMD_JWT_TOKEN = "<paste token here>"
```

These environment variables are not persisted — set them each session, or add them to a local `.env` file that is **not committed** to the repo.

---

## Scripts

### `create_custom_classification.py`

Creates the `DataElementType` classification and all its tags in OpenMetadata,
reading definitions from `taxonomies\data-element-type.yaml`.

**Idempotent** — safe to run multiple times; existing classifications and tags are
skipped, not overwritten.

```powershell
$env:OMD_HOST      = "http://localhost:8585/api"
$env:OMD_JWT_TOKEN = "<token>"
.venv\Scripts\python scripts\create_custom_classification.py
```

**Expected output:**

```
Loaded taxonomy 'DataElementType' from data-element-type.yaml (35 tags)
  [created] classification: DataElementType
  [created] tag: DataElementType.Address
  [created] tag: DataElementType.Address.StreetAddress
  ...
Done.
```

On a re-run all lines will show `[skip]` instead of `[created]`.

**To update the taxonomy**, edit `taxonomies\data-element-type.yaml` and re-run the
script. Note: the script does not delete or rename existing tags — do that manually
in the OMD UI if needed.

---

## Running Auto-Classification

After tags have been upserted with recognizer configs, run the AutoClassification workflow.

> **Before first run**: verify `serviceName` in `workflows\adventureworks-autoclassify.yaml`
> matches exactly what appears in OMD under **Settings → Services**. Edit the file if needed.

```powershell
$env:OMD_HOST      = "http://localhost:8585/api"
$env:OMD_JWT_TOKEN = "<token>"

# Step 1 — upsert tags with recognizer configs (safe to re-run anytime)
.venv\Scripts\python scripts\create_custom_classification.py

# Step 2 — run the classification workflow
.venv\Scripts\metadata.exe classify -c workflows\adventureworks-autoclassify.yaml
```

After the workflow completes, check results in the OMD UI by navigating to an
AdventureWorks table (e.g. `Person.Person`) and confirming `DataElementType.*`
tags appear on columns like `EmailAddress`, `FirstName`, `LastName`, `Phone`.

### Verifying recognizer config via API

The OMD UI does not expose `autoClassificationEnabled` or `recognizers` on tag
detail pages. Use the REST API to confirm they were written correctly:

```powershell
curl -s `
  -H "Authorization: Bearer $env:OMD_JWT_TOKEN" `
  "http://localhost:8585/api/v1/tags/name/DataElementType.Email?fields=recognizers,autoClassificationEnabled,autoClassificationPriority" `
  | python -m json.tool
```

Expected: `"autoClassificationEnabled": true` and a non-empty `"recognizers"` array.

### Promoting to prod

```powershell
$env:OMD_HOST      = "https://your-prod-omd.example.com/api"
$env:OMD_JWT_TOKEN = "<prod-token>"
.venv\Scripts\python scripts\create_custom_classification.py
.venv\Scripts\metadata.exe classify -c workflows\adventureworks-autoclassify.yaml
```

---

## Applying DataElementType Tags

Applies `DataElementType.*` tags to columns by matching column names against the patterns
defined in `taxonomies\data-element-type.yaml`. Columns that already carry any
`DataElementType.*` tag are skipped (safe to re-run).

```powershell
$env:OMD_HOST      = "http://localhost:8585/api"
$env:OMD_JWT_TOKEN = "<token>"

# Dry run first — see what would be tagged without writing
.venv\Scripts\python scripts\apply_data_element_tags.py `
  --service "Desktop DB" `
  --database "AdventureWorks2019" `
  --schema-include "^Person$" "^HumanResources$" `
  --dry-run

# Apply for real
.venv\Scripts\python scripts\apply_data_element_tags.py `
  --service "Desktop DB" `
  --database "AdventureWorks2019" `
  --schema-include "^Person$" "^HumanResources$"
```

**To promote to prod:** set `$env:OMD_HOST` and `$env:OMD_JWT_TOKEN` to prod values and re-run.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'metadata'` | venv not activated or install incomplete | Run the setup install command again |
| `Missing required environment variable: OMD_JWT_TOKEN` | Env vars not set | Set `$env:OMD_HOST` and `$env:OMD_JWT_TOKEN` |
| `401 Unauthorized` | Token expired or wrong | Refresh the token from the OMD UI |
| `WinError 32` during pip install | VS Code Python extension locking venv files | Close VS Code, delete `.venv`, reinstall |
| OMD UI not reachable | Docker containers not running | `docker compose up -d` and wait ~60s |
| `patch() raises validation error` | Column tag payload rejected | Check TagLabel fields: `tagFQN`, `labelType`, `state`, `source` are all set |
