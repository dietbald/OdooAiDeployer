# Setting up a new Odoo instance

End-to-end walkthrough for adding a new Odoo database to the deployment system.

## Prerequisites

- `gh` CLI authenticated as a user with repo creation rights in the target org
- Python 3.10+ with `git+https://github.com/dietbald/OdooAiDeployer.git@main` installable
- Odoo admin credentials (URL, DB, username, password/API key) for each of dev, staging, production

## Steps

### 1. Create the empty GitHub repo

```bash
gh repo create dietbald/MyInstanceOdoo --private
```

### 2. Run the bootstrap script

From an OdooAiDeployer checkout:

```bash
python scripts/bootstrap_instance_repo.py \
  --owner dietbald \
  --repo MyInstanceOdoo \
  --instance-name my-instance \
  --description "What this Odoo instance is for" \
  --odoo-version 19 \
  --edition online \
  --companies CompanyA CompanyB
```

This will:
- Clone the empty repo to a temp dir
- Copy `templates/instance-repo-template/` into it
- Substitute placeholders in `config/instance.yaml` and `README.md`
- Make initial commit + push
- Create `dev`, `staging`, `production` GitHub environments

### 3. Add credentials per environment

```bash
for env in dev staging production; do
  gh secret set ODOO_URL      --env "$env" --repo dietbald/MyInstanceOdoo
  gh secret set ODOO_DB       --env "$env" --repo dietbald/MyInstanceOdoo
  gh secret set ODOO_USERNAME --env "$env" --repo dietbald/MyInstanceOdoo
  gh secret set ODOO_PASSWORD --env "$env" --repo dietbald/MyInstanceOdoo
done
```

### 4. Add required reviewers on staging + production

In the GitHub UI: **Settings → Environments → staging → Required reviewers**, add yourself. Repeat for production.

### 5. Capture the baseline

```bash
gh workflow run export-baseline.yml --repo dietbald/MyInstanceOdoo -f env=production
```

Snapshots the customization layer (views, server actions, automations, crons, custom field definitions, menus, mail templates, ACLs, record rules, custom models) into `baseline/production/`. Skips business data (products, partners, orders, invoices, employees) by design. Run before authoring any changeset so you have a known-good reference for diffs and rollback planning.

### 6. Author your first changeset

```bash
git checkout -b ai/001_your_first_change
mkdir changesets/001_your_first_change
# write manifest.yaml + content files
git add . && git commit -m "changeset 001"
git push -u origin ai/001_your_first_change
```

GitHub Actions runs `validate.yml` then `deploy-dev.yml`. If both pass, open a PR to `main`.

After merge, manually trigger `promote-staging.yml` and (after approval + final tests) `promote-prod.yml`.
