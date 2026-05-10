# config/blocklist/

Plain-text lists of things AI is not allowed to touch in a changeset. The static validator (`odoo-deploy validate`) rejects any operation whose target appears here.

One entry per line. `#` starts a comment.

| File | Effect |
|------|--------|
| `models.txt` | Block any operation whose `model:` matches a listed model name |
| `xml_ids.txt` | Block any operation whose `xml_id:` matches |
| `operation_types.txt` | Block changesets that use the listed operation types |

To unblock something temporarily: TJ removes the line on a manual branch and merges. AI cannot bypass.

The default `models.txt` ships with the most dangerous Odoo models pre-listed (auth, accounting, payroll, payments). Tune per instance.
