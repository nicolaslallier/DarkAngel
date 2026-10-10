# Home dashboard: backups, infra, invoices

Date: 2026-10-10 · Status: draft, awaiting review

## Intent

The home page (`HomeView.vue`) only shows the API health and the signed-in user.
It becomes a dashboard where every household member sees, at a glance:

1. **Backups**: for each Portainer instance (Heaven, Infra, ...), the latest
   archive in the `portainer-backups` SeaweedFS bucket (made by
   `scripts/portainer-backup.sh`): when, how big, and an alert when it is too old.
2. **Infra**: for each Portainer instance, reachable or not, Portainer version,
   number of environments and stacks.
3. **Invoices**: upcoming and overdue invoices, contract renewals, and the number
   of invoices waiting in the review queue.

Success: opening `/` answers "is anything broken or late?" without opening
Portainer or another page. One failing source never blanks the other cards.

Out of scope (YAGNI): container-level health, charts of the history (it is stored,
not drawn), notifications, editing anything from the dashboard, per-role hiding.

## Decisions taken

- **Collector, not live calls.** A separate process polls Portainer and S3 and
  writes to PostgreSQL; the API only reads. The Portainer API keys (Docker-root
  tokens) never reach the `darkangel-api` container.
- **Collector = second service of `deploy/portainer-stack.yml`**, same backend
  image, command `python -m app.collector`. No new image or CI job.
- **Visible to every household member** (`Reader` dependency). The infra tables
  are not scoped by `household_id`: infra is one per deployment. This deviates
  from the "every query filters on `household_id`" rule on purpose.
- **Invoices reuse existing endpoints** (`/api/upcoming` and the review-queue
  count); only `/api/infra` is new.

## Components

### Collector (`backend/app/collector/`)

- `portainer.py`: per instance, read-only GETs with the `X-API-KEY` header:
  `/api/status` (version), `/api/endpoints` (environments), `/api/stacks`.
  Short timeout; `insecure` option for self-signed certificates.
- `backups.py`: lists `<instance slug>/` in the backup bucket with boto3 and keeps
  the newest object (`LastModified`, size, key). The slug rule is the script's:
  lower-case, `_` to `-`.
- `main.py`: loop every `collector_interval_seconds` (300). Errors are caught per
  instance: an unreachable instance becomes a row with `reachable=false` and the
  error text, never a crash. A PostgreSQL failure fails that pass; the next one
  retries. Each pass also deletes rows older than 30 days.

### Data (Alembic migration, append-only)

- `infra_status(instance, checked_at, reachable, version, environments, stacks, error)`
- `backup_status(instance, checked_at, last_backup_at, size_bytes, object_key)`
  (`last_backup_at` null = bucket has no archive for that instance)
- A new `repositories/infra.py` with in-memory fake in `tests/conftest.py`, like
  the other repositories.

### Configuration (`core/config.py`, `DARKANGEL_` prefix)

- `portainer_instances`: JSON list of `{name, url, api_key, insecure?}`.
- `backup_s3_endpoint`, `backup_s3_bucket` (`portainer-backups`),
  `backup_s3_access_key`, `backup_s3_secret_key`: a **read-only** identity,
  provisioned in Infra (`make s3-provision`). Distinct from the writer identity
  of the backup script.
- `backup_max_age_hours` (48), `collector_interval_seconds` (300).
- In the stack file these arrive as stack variables set on the collector service
  only. `darkangel-api` reads `backup_max_age_hours` and nothing secret.

### API

`GET /api/infra` (`Reader`) returns the latest row per instance:

```json
{
  "instances": [{"name": "heaven", "reachable": true, "version": "2.21.0",
                 "environments": 2, "stacks": 7, "checked_at": "...", "error": null}],
  "backups":   [{"instance": "heaven", "last_backup_at": "...", "size_bytes": 123456,
                 "age_hours": 5.2, "stale": false}]
}
```

`stale` is computed at read time: no archive, or older than `backup_max_age_hours`.
`checked_at` lets the UI flag a stopped collector (older than 15 minutes).
The API container never receives `portainer_instances` (it holds the keys), so it
only knows instances that have at least one row; before the first collector pass the
lists are empty and the UI says "nothing collected yet".

### Frontend

- `api/infra.ts` (types + `apiGet`), `stores/infra.ts` (Pinia setup store with
  `loading`/`error`/data, same shape as the others).
- `HomeView.vue` becomes a three-card grid (Backups, Infra, Invoices) and keeps the
  "signed in as" line. Each card loads independently and has its own loading and
  error state. Red = backup stale, instance unreachable, overdue invoice, stale
  collector data. The invoice card links to `/invoices/review` and `/providers`.

## Errors and edge cases

- Instance unreachable / bad key / non-JSON reply: recorded, shown red.
- Bucket empty or unreadable: `last_backup_at` null, shown as "no backup" (a
  transient S3 error therefore alerts until the next pass, five minutes later).
- Collector not running: `checked_at` goes stale, the UI says so.
- No `portainer_instances` configured: the collector idles and logs once; the
  infra and backup cards show "nothing configured".

## Testing (per `docs/testing.md`)

- Unit: collector with fake Portainer (`httpx.MockTransport`) and fake S3: reachable,
  unreachable, bad JSON, empty bucket, newest-object choice, retention delete.
- Unit: `stale` / `age_hours` computation, with the injected clock.
- Regression: pinned shape of `/api/infra`.
- Vitest: store and `HomeView` (loading, error, empty, stale, per-card isolation).
- Docs: `docs/dashboard.md`; `README.md` and `deploy/portainer-stack.yml` comments
  list the new stack variables.

## Open items for the plan

- Exact name of the read-only S3 identity and the Infra command that provisions it.
- Whether `/api/infra` should also expose the 30-day history (not now).
