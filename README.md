# OdooAiDeployer

Reusable, AI-safe deployment system for Odoo Online customizations.

This repository is the **deployment engine**. It contains the validators, XML-RPC handlers, rollback engine, baseline exporter, and GitHub Actions workflow templates. It does **not** contain any instance-specific customizations.

Each Odoo database lives in its own **instance repository** (e.g. [`BiccOdoo`](https://github.com/dietbald/BiccOdoo), [`ContourOdoo`](https://github.com/dietbald/ContourOdoo), [`AutopilotOdoo`](https://github.com/dietbald/AutopilotOdoo)) which holds that instance's baseline, changesets, audits, rollback snapshots, config, and CI workflows.

## The split

```
github.com/dietbald/
├── OdooAiDeployer                ← this repo (the engine)
├── BiccOdoo                      ← BICC + IGEBC shared instance
├── ContourOdoo                   ← Contour Operations
└── AutopilotOdoo                 ← Autopilot Pte Ltd
```

The engine is `pip install`ed by each instance's CI workflows:

```yaml
- run: pip install "git+https://github.com/dietbald/OdooAiDeployer.git@main"
- run: odoo-deploy --repo . deploy --env dev --changeset 001_fix_404_page
```

## What the engine provides

- **9 typed handlers** — `update_view`, `create_view`, `create_field`, `create_server_action`, `create_automated_action`, `create_cron`, `create_menu`, plus restricted `create_record` / `update_record`.
- **Static validator** — XML well-formedness, Python AST + forbidden-import check, blocklist enforcement, manifest schema, restricted-folder guard for AI branches. Outputs `reports/validation/<id>.json` and `reports/ai_feedback/<id>.md`.
- **Odoo-aware preflight** — runs against dev DB to catch missing models / fields / view keys before any write.
- **Idempotent deploy** — every handler reads current state first; if it already matches, skip + log, no backup, no audit churn.
- **Two-layer audit** — `ir.config_parameter` row inside the Odoo DB (travels with clones) plus git-tracked `audits/<env>/<id>.json` files (the promotion gate).
- **Promotion gate** — `production` refuses unless `dev` AND `staging` audits exist with matching `manifest_sha256`. Content edits after a lower env's apply force a re-promotion.
- **Operation-level rollback** — pre-write snapshots in `rollback_snapshots/<env>/<id>/`; rollback workflow restores in reverse order.
- **5 GitHub Actions workflows** — `validate`, `deploy-dev`, `promote-staging`, `promote-prod`, `rollback`. Approvals via GitHub environment protection rules.

> **Out of scope for v1:** baseline export and drift detection. Pure changeset-driven tracking — the in-DB registry says what's been deployed; per-operation rollback snapshots cover undo. Snapshotting all of Odoo's customization records into git would be ~150 MB of mostly-noise (stock module fields, stock views) and only earns its keep once we want drift detection. Deferred to V2 when there's a real reason to add it.

## Three environments

| Env | Purpose | AI access | Deploy by |
|---|---|---|---|
| `dev` | Iteration sandbox; auto-deploys on push to `ai/*`; `--force` allowed | Yes (deploy + verify) | AI or TJ |
| `staging` | Final validation on a fresh prod clone | Read-only verify only | TJ via gh env approval |
| `production` | Real prod | None | TJ via gh env approval |

## CLI reference

```
odoo-deploy --repo <instance-repo-path> <subcommand> [args]

  validate         Static validation of a changeset (no Odoo)
  preflight        Odoo-aware validation (read-only)
  deploy           Apply a changeset to an env
  verify           Read-only state check
  rollback         Restore a previous deployment
  status           Show audit + registry state across envs
```

Required env vars (set as GitHub environment secrets in CI; sourced from `envs/<env>.env` locally):

```
ODOO_URL ODOO_DB ODOO_USERNAME ODOO_PASSWORD
```

## AI boundaries

AI may edit only:

```
changesets/**
reports/ai_feedback/**
docs/changeset-notes/**
```

AI must not edit `deployer/**`, `.github/workflows/**`, `config/**`, or audits / rollback snapshots for `staging`/`production`. The validator's restricted-folder guard fails CI for `ai/*` branches that touch any of these.

If AI needs a new operation type, ask TJ to add a handler under `deployer/handlers/` and register it in `DISPATCH`. Inline Python deploy code in changesets is never accepted.

## Set up a new instance repository

```bash
python scripts/bootstrap_instance_repo.py \
  --owner dietbald \
  --repo BiccOdoo \
  --instance-name bicc-igebc \
  --description "BICC + IGEBC shared Odoo instance" \
  --odoo-version 19 \
  --edition online
```

The script clones the empty GitHub repo, copies `templates/instance-repo-template/` into it, fills the placeholders in `config/instance.yaml`, creates the three GitHub environments via the API, and prints manual instructions for adding the credential secrets and required reviewers.

After bootstrap, you author your first changeset — there's no baseline-export step. Pure changeset-driven flow.

## Layout

```
OdooAiDeployer/
├── deployer/                  # the engine (TJ-edited only)
│   ├── cli.py                 # `odoo-deploy` entry point
│   ├── deploy.py              # apply a changeset
│   ├── verify.py              # read-only state check
│   ├── validate_changeset.py  # static validation
│   ├── preflight.py           # Odoo-aware validation
│   ├── rollback.py            # operation-level rollback
│   ├── audit.py               # audit files + in-DB registry
│   ├── hash_changeset.py      # content sha256
│   ├── odoo_client.py         # XML-RPC connection
│   ├── github_setup.py        # gh API helpers
│   └── handlers/              # one module per operation type
├── templates/
│   ├── instance-repo-template/   # what bootstrap copies into a new instance
│   └── env-example-files/        # local-dev .env templates
├── scripts/
│   └── bootstrap_instance_repo.py
├── docs/
│   ├── architecture.md
│   ├── changeset-format.md
│   └── setup-new-odoo-instance.md
├── tests/
├── pyproject.toml
└── requirements.txt
```

## Status

V1 — being bootstrapped. See [`docs/architecture.md`](docs/architecture.md) for the canonical design.
