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

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'metadata'` | venv not activated or install incomplete | Run the setup install command again |
| `Missing required environment variable: OMD_JWT_TOKEN` | Env vars not set | Set `$env:OMD_HOST` and `$env:OMD_JWT_TOKEN` |
| `401 Unauthorized` | Token expired or wrong | Refresh the token from the OMD UI |
| `WinError 32` during pip install | VS Code Python extension locking venv files | Close VS Code, delete `.venv`, reinstall |
| OMD UI not reachable | Docker containers not running | `docker compose up -d` and wait ~60s |
