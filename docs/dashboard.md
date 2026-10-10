# Home dashboard

The home page (`/`) shows three cards: **Backups** (the newest Portainer backup
archive in the bucket `portainer-backups`), **Infra** (one row per Portainer
instance: reachable or not, how many stacks and containers run) and
**Invoices** (overdue and upcoming dues from the service-providers feature).

## How it works

A separate process, `python -m app.collector` (compose service
`darkangel-collector`, same image as the API), polls every five minutes: the
GET endpoints of each Portainer instance, and a listing of the backup bucket.
It writes what it saw to append-only PostgreSQL tables and deletes rows older
than 30 days. `GET /api/infra` reads the latest rows; the SPA never talks to
Portainer or S3. The collector is the only service that holds Portainer API
keys. It runs no migration (`darkangel-api` does); on the very first deploy a
pass can fail for want of the tables, it logs and retries.

## Stack variables

Set in `.portainer.env` (see `.portainer.env.example`; `make up` hands them to
the stack) and as GitHub repository secrets of the same name (the deploy
workflow rewrites the stack's whole environment on every run).

| Variable / secret | Meaning |
|---|---|
| `PORTAINER_INSTANCES` | JSON list on one line: `[{"name":"HEAVEN","url":"https://...","api_key":"..."}]` |
| `BACKUP_S3_SECRET_KEY` | secret of the read-only S3 identity `portainer-backups-ro` |

## One-time setup

1. In each Portainer instance, create an access token (My account -> Access
   tokens) and put it in `PORTAINER_INSTANCES` as that instance's `api_key`.
2. In the Infra repo, create the read-only identity on the bucket
   `portainer-backups`. The intended shape is
   `make s3-provision app=portainer-backups-ro bucket=portainer-backups`.
   **Not confirmed:** this repo does not contain the Infra repo, so the exact
   flags (read-only mode, no versioning flag) must be checked against the Infra
   README before running, and the command actually run should be noted here.
   Put the printed secret in `BACKUP_S3_SECRET_KEY`.
3. Create both GitHub secrets, then `make up`.

## Reading the colours

- Backup red: the newest archive is older than 48 hours, or there is none.
- Infra red: the instance was unreachable on the last pass (the row says why).
- Invoices red: at least one invoice is overdue.
- Banner "data older than 15 minutes": the collector has stopped; the cards
  show its last pass, not the present.

## Access

`/api/infra` is visible to every member of any household: the data is global
per deployment, not scoped by household. The `error` text of an unreachable
instance is only an exception class and, for HTTP failures, the status (for
example `ConnectError` or `HTTPStatusError 401`); the full detail, URL included,
is in the collector log.

Rows of an instance more than 15 minutes behind the newest reading are not
shown, so a removed or renamed instance disappears after one pass.

## Debugging

`docker logs` of `darkangel-collector` shows each pass and its failures. Empty
cards mean no rows yet (collector not started, or `PORTAINER_INSTANCES` unset:
`make up` warns about it).

If every instance shows red "No backup", read the collector logs first: a wrong
or missing `portainer-backups-ro` secret looks the same as failing backups.

## Metrics panels

Below the three cards the home page shows a metrics section with three rows and
a range picker (1h, 6h, 24h, 7d):

- **Host**: CPU, memory, disk on `/`, network in and out. Source: node-exporter,
  read through Prometheus.
- **Containers**: the top five containers by CPU and by memory. Source: cAdvisor,
  read through Prometheus.
- **Portainer**: reachable, stacks and backup size per instance. Source: the
  PostgreSQL rows the collector writes (not Prometheus).

`GET /api/metrics/range?panel=<id>&range=<1h|6h|24h|7d>` serves them. The client
sends a panel id, never a query: the PromQL lives in `backend/app/metrics/catalog.py`.
The API reads Prometheus at `DARKANGEL_PROMETHEUS_URL` (setting
`prometheus_url`), which defaults to `http://prometheus:9090`. It is optional and
is not a stack variable, so the stack needs nothing unless Prometheus lives
somewhere else.

Network (**not verified from this repo**): `darkangel-api` is only on the external
network `infra-net`, so `http://prometheus:9090` works only if the Prometheus
container is on `infra-net`. If it is not, point `DARKANGEL_PROMETHEUS_URL` at an
address the API can reach. Check with
`docker inspect <prometheus container> --format '{{json .NetworkSettings.Networks}}'`
on the Docker host.

If Prometheus is down, only the Host and Containers panels go blank (the endpoint
answers 502). The Portainer row reads PostgreSQL and keeps working.

Adding a panel: a prom panel is one entry in `PANELS` in
`backend/app/metrics/catalog.py` plus its id in the pinned list in
`backend/tests/regression/test_metrics_catalog.py`; a db panel also needs a branch
in `backend/app/api/routes/metrics.py` (an unhandled one answers 500).
The OpenAPI snapshot does not change, because the panel ids are not part of the
response schema.

History: Portainer history is kept 30 days by the collector, which is longer than
any range offered (max 7d). How far back Prometheus
keeps the Host and Containers data is set in the Prometheus configuration, which
this repo does not contain (**not verified**).
