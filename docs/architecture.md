# Odoo Customization Deployment Architecture

## 1. Purpose

This document defines the architecture for a safe, repeatable, AI-assisted deployment system for Odoo Online customizations.

The goal is to solve these current problems:

- AI or developers sometimes make changes directly on production.
- Staging and production deployments may not be identical.
- Odoo XML-RPC scripts are currently ad hoc.
- Failed changes are hard to trace, test, or roll back.
- There is no clean promotion path from development to staging to production.
- Existing Odoo customizations are not yet fully captured in Git.

The new system separates:

1. **The deployment engine** — reusable code that validates, deploys, verifies, and rolls back Odoo changes.
2. **The Odoo instance repositories** — one GitHub repository per Odoo database/codebase containing baseline exports, changesets, audit files, and deployment configuration.

Important clarification:

> BICC and IGEBC are in the same Odoo instance, so they share one Odoo instance repository and one deployment history.

---

## 2. High-Level Architecture

```text
AI / Developer
   |
   | creates changeset files
   v
Odoo Instance GitHub Repository
   |
   | GitHub Actions validates
   v
Auto Deploy to Dev
   |
   | if successful
   v
Manual Promotion to Staging / Pre-Prod
   |
   | if approved and tested
   v
Manual Promotion to Production
   |
   | with rollback snapshot and audit trail
   v
Production Odoo
```

The AI does **not** directly deploy to Odoo.

The AI only creates or edits files in the Odoo instance repository.

Deployment credentials are stored only in GitHub Actions environment secrets.

---

## 3. Repository Strategy

### 3.1 Master Repository

`OdooAiDeployer` — the reusable deployment platform. Contains the deployer code, validators, XML-RPC handlers, rollback logic, GitHub Actions templates, bootstrap scripts, and documentation. Does **not** contain instance-specific customizations. AI should not casually modify this repository — changes here are changes to the deployment platform itself.

### 3.2 Instance Repositories

One per Odoo database. Examples: `BiccOdoo` (BICC + IGEBC shared), `ContourOdoo`, `AutopilotOdoo`. Holds that instance's baseline, changesets, audits, rollback snapshots, config, and CI workflows.

---

## 4. Repository Ownership Rules

### 4.1 AI-Editable Areas

```
changesets/**
reports/ai_feedback/**
docs/changeset-notes/**
```

AI may create new changeset folders and modify changesets that are still in dev iteration.

### 4.2 AI-Restricted Areas

```
deployer/**
.github/workflows/**
config/**
audits/staging/**
audits/production/**
rollback_snapshots/staging/**
rollback_snapshots/production/**
baseline/prod/**
```

If any AI-generated branch modifies these areas, GitHub Actions fails validation.

---

## 5. Deployment Flow

### 5.1 Development Flow

```
1. AI creates changeset folder.
2. AI commits and pushes to GitHub branch.
3. GitHub Actions runs static validation.
4. If static validation passes, GitHub Actions deploys to dev.
5. Dev deploy result is recorded.
6. If dev fails, GitHub Actions outputs clear feedback.
7. AI fixes the changeset and pushes again.
8. Repeat until dev passes.
```

Dev is the only environment where automatic deployment is allowed.

### 5.2 Staging / Pre-Prod Flow

```
1. Dev deployment must pass.
2. Changeset must have a fixed git_commit_sha.
3. Changeset must have a fixed changeset_sha256.
4. TJ manually triggers staging deployment.
5. GitHub Actions waits for staging environment approval.
6. After approval, staging secrets become available.
7. The exact same commit and changeset hash are deployed.
8. Staging post-deploy verification runs.
9. Audit and rollback snapshot are stored.
```

### 5.3 Production Flow

```
1. Dev deployment must pass.
2. Staging deployment must pass.
3. Production deployment must use the exact same git_commit_sha.
4. Production deployment must use the exact same changeset_sha256.
5. No failed_partial deployment is allowed in the promotion chain.
6. Rollback snapshot must be prepared.
7. TJ manually approves production environment deployment.
8. GitHub Actions deploys to production.
9. Post-deploy verification runs.
10. Production audit file is created.
11. Rollback option remains available.
```

Production deployment is never automatic.

---

## 6. GitHub Actions Workflows

```
.github/workflows/
├── validate.yml
├── deploy-dev.yml
├── promote-staging.yml
├── promote-prod.yml
├── rollback.yml
└── export-baseline.yml
```

### validate.yml

Triggered on push, pull_request, workflow_dispatch. Detects changed changesets, validates manifest schema, validates referenced files exist, checks XML well-formedness, parses Python with AST, blocks forbidden imports/opcodes, computes `changeset_sha256`, checks AI didn't edit restricted folders, produces `validation_report.json` and `ai_feedback.md`, adds GitHub error annotations.

### deploy-dev.yml

Triggered after validation passes, or manually. Loads dev environment secrets, runs Odoo-aware validation against dev, deploys, verifies target state, creates rollback snapshot, creates dev audit file. Allows re-apply.

### promote-staging.yml

Triggered manually. Verifies dev passed and `changeset_sha256` matches. Waits for staging environment approval. Loads staging secrets, deploys, verifies, creates rollback snapshot and audit. No force allowed.

### promote-prod.yml

Triggered manually. Verifies dev AND staging passed with matching sha. Verifies rollback snapshot exists. Waits for production environment approval. Deploys, verifies, creates production audit. No force allowed.

### rollback.yml

Triggered manually. Loads rollback snapshot, shows preview, requires manual approval for staging/production, restores previous values operation-by-operation, verifies, creates rollback audit file.

### export-baseline.yml

Triggered manually. Connects to the chosen env, exports supported customization records into `baseline/<env>/`, commits.

---

## 7. GitHub Environments

Three environments per instance repo: `dev`, `staging`, `production`. Each has its own secrets (`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`).

```
dev:        approval required: no   auto deploy: yes
staging:    approval required: yes  required reviewer: TJ   auto deploy: no
production: approval required: yes  required reviewer: TJ   auto deploy: no
```

Environment secrets are not available to jobs until the environment deployment is approved.

---

## 8. Changeset Format

Each change is a numbered folder under `changesets/`. See [`changeset-format.md`](changeset-format.md) for the full schema, supported operation types, and examples.

---

## 9. Supported Operation Types (V1)

```
update_view
create_view
create_field
create_server_action
create_automated_action
create_cron
create_menu
update_record
create_record
```

`create_record` and `update_record` are restricted: they require `allow_generic_records: true` in the manifest AND the target model must be on the whitelist in `deployer/handlers/create_record.py:MODEL_WHITELIST`.

---

## 10. Changeset Hashing

Every deployment is pinned to two identifiers: `git_commit_sha` and `changeset_sha256`.

The `changeset_sha256` includes manifest.yaml, all referenced XML/Python/CSV/JSON/YAML files, and relative file paths. It does NOT include README.md (intentional — see `deployer/hash_changeset.py`), generated reports, audit files, or rollback files.

The deployer rejects promotion if the current changeset hash differs from the hash that passed the previous environment.

---

## 11. Validation Layers

### 11.1 Static Validation

Runs without Odoo access (`validate.yml`):

- valid YAML, manifest schema match, id == folder name
- operation type registered in DISPATCH
- referenced files exist
- XML well-formed
- Python parses (AST)
- forbidden imports/opcodes/calls in server-action bodies
- generic operations require `allow_generic_records: true`
- targets not in `config/blocklist/`
- restricted repo areas not modified by AI branch

Forbidden patterns in server-action / cron / automation Python:

```
import   __import__   eval   exec   compile   open
from <stdlib> import   os.*   sys.*   subprocess.*   socket.*
```

### 11.2 Odoo-Aware Validation

Runs against dev DB before deployment (`preflight`):

- target model exists
- target fields exist
- target xml_ids exist
- view key exists
- inherited view exists
- user has write access
- current state can be read

---

## 12. AI Feedback Loop

Validation and deploy failures produce `reports/ai_feedback/<changeset_id>.md` plus GitHub error annotations. AI reads the markdown and corrects the changeset. The feedback file lists exact file:line locations and the fix scope ("Edit only `changesets/<id>/`").

---

## 13. Baseline Export & Drift Detection

### 13.1 What to Export

Minimum baseline (per environment):

```
ir.ui.view
ir.actions.server
base.automation
ir.cron
ir.model.fields
ir.ui.menu
ir.actions.act_window
mail.template
ir.model.access
ir.rule
website.page
```

Each record records: model, db_id, xml_id (if any), key (if any), name, write_date, payload, sha256, exported_at.

### 13.2 Drift Detection

A scheduled or manual workflow compares current production Odoo against `baseline/prod`. Detects Odoo Studio changes, manual view edits, untracked server actions, etc. If drift is detected: create drift report, block production deployment until reviewed, either import drift into Git as a changeset or revert unauthorized changes.

---

## 14. Rollback Design

Rollback is operation-level. Before each write the deployer stores the previous state. Rollback restores in reverse order.

Rules:

```
dev:        rollback automatic
staging:    rollback requires TJ approval
production: rollback requires TJ approval; rollback creates rollback audit; rollback verifies restored state
```

A Git revert alone does NOT change Odoo production. Two rollback options: **operational rollback** (restore from snapshot, fast) or **rollback changeset** (new changeset that reverses the change, cleaner long-term history).

---

## 15. Audit Design

`audits/<env>/<changeset>.json` — written by the runner after every apply. Contains: changeset id, environment, git_commit_sha, manifest_sha256, status (deployed | failed_partial), started_at, finished_at, approved_by, per-operation results with rollback_snapshot paths.

Promotion gate reads these audits.

---

## 16. Failed Partial Deployments

When operation N succeeds but N+1 fails, the audit records `status: failed_partial`, `failed_operation: N+1`, `completed_operations: [0..N]`, and the error. Failed_partial blocks promotion. Must be fixed or rolled back before the changeset can move forward.

---

## 17. Security Model

### 17.1 Credential Access

AI never receives `ODOO_PASSWORD`, production/staging XML-RPC credentials, or GitHub environment secrets.

### 17.2 Environment Restrictions

```
dev:        accessible through GitHub Actions auto deployment
staging:    protected by GitHub environment approval
production: protected by GitHub environment approval; only TJ can approve
```

### 17.3 Branch Model

```
main:     production-ready history
ai/*:     AI-created branches
dev/*:    experimental developer branches
hotfix/*: urgent manual fixes
```

AI pushes to `ai/*`. Validation runs and dev deploys. TJ reviews and merges to `main`. Staging and production deploy only from `main`.

---

## 18. Minimum Viable V1

```
- master deployer repo
- one BICC/IGEBC instance repo
- baseline exporter
- static validator
- Odoo-aware validator
- changeset hashing
- deploy to dev through GitHub Actions
- manual staging deployment with environment approval
- manual production deployment with environment approval
- operation-level rollback snapshot
- rollback workflow
- AI feedback markdown report
```

No web dashboard in V1 — GitHub Actions UI is enough.

---

## 19. Final Principle

```
One master repository contains the deployment system.
One repository per Odoo instance contains that instance's baseline,
changesets, audits, and config.
```
