# {{INSTANCE_NAME}} — Odoo Customizations

Instance repository for the {{INSTANCE_NAME}} Odoo database. Managed through the [OdooAiDeployer](https://github.com/dietbald/OdooAiDeployer) deployment system.

## What lives here

- `changesets/<id>/` — one folder per change. AI may add and edit these on `ai/*` branches.
- `baseline/<env>/` — exported snapshot of Odoo customization records. Used as a known-good reference for diffs and drift detection.
- `audits/<env>/<id>.json` — promotion proof, written by the runner after each successful deploy.
- `rollback_snapshots/<env>/<id>/` — pre-write content snapshots used by the rollback workflow.
- `reports/validation/`, `reports/deployment/`, `reports/ai_feedback/` — CI outputs, including the markdown feedback file the AI reads when validation fails.
- `config/instance.yaml` — instance identity (name, odoo version, edition, companies).
- `config/blocklist/` — text files listing things AI is not allowed to touch (models, xml_ids, operation types).
- `.github/workflows/` — validate, deploy-dev, promote-staging, promote-prod, rollback, export-baseline.

## Deploy flow

```
ai/* branch → push
    ↓
.github/workflows/validate.yml          ← static validation
    ↓ (passes)
.github/workflows/deploy-dev.yml        ← auto-deploy to dev
    ↓ (passes)
PR review + merge to main
    ↓
.github/workflows/promote-staging.yml   ← manual trigger; gh env approval; deploys to staging
    ↓
.github/workflows/promote-prod.yml      ← manual trigger; gh env approval; deploys to production
```

Production refuses deployment unless dev AND staging both have a passing audit file with matching `manifest_sha256`.

## Boundaries

| Path | AI can edit? |
|------|---|
| `changesets/**` | Yes |
| `reports/ai_feedback/**` | Yes (CI writes here too) |
| `docs/changeset-notes/**` | Yes |
| `deployer/**` | No |
| `.github/workflows/**` | No |
| `config/**` | No |
| `audits/staging/**`, `audits/production/**` | No |
| `rollback_snapshots/staging/**`, `rollback_snapshots/production/**` | No |
| `baseline/prod/**` | No |

CI fails validation if an `ai/*` branch modifies any restricted path.

## Quick commands

```bash
# (Local, against dev — needs ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_PASSWORD in env)
odoo-deploy --repo . validate --changeset 001_fix_404_page
odoo-deploy --repo . deploy --env dev --changeset 001_fix_404_page --dry-run
odoo-deploy --repo . status --changeset 001_fix_404_page
```
