# Grafana-like metrics panels on the dashboard

Date: 2026-10-10 · Status: draft, awaiting review

## Intent

The home dashboard (see `2026-10-10-home-dashboard-design.md`) shows point-in-time
status cards. The owner wants it to feel like Grafana: **history graphs** with a
time-range picker and auto-refresh, for the host, the containers and the Portainer
instances. Success: opening the dashboard answers "what has been happening over the
last hour/day/week?" without leaving DarkAngel or opening Grafana.

Out of scope (YAGNI): drag/resize, custom or saved dashboards, alerting, raw PromQL
from the client, running Grafana itself.

## Decisions taken

- **Prometheus already exists** (`http://prometheus:9090`, no auth; node-exporter and
  cAdvisor feed it). DarkAngel stores no host samples; the API proxies `query_range`.
- **The browser only talks to `/api`** (Keycloak token). Prometheus is never exposed.
- **Fixed server-side PromQL catalog.** The client sends a panel id and a range,
  never a query. This keeps an unauthenticated Prometheus from being reachable
  through us with arbitrary PromQL.
- **uPlot** (new frontend dependency, ~50 KB) draws the graphs; panels sit in a CSS
  grid with a fixed layout.
- **Portainer-row panels come from our DB** (`infra_status`, `backup_status`, already
  append-only, 5 min sampling, 30 day retention) and are shaped into the same
  series format, so the frontend cannot tell the two sources apart.
- **Access:** same `Reader` dependency as `/api/infra` (any household member).
  Metrics are per deployment, not per household.

## Backend (`backend/app/`)

- `core/config.py`: `prometheus_url` (default `http://prometheus:9090`),
  `prometheus_timeout_seconds` (default 5).
- `metrics/catalog.py`: the fixed panel list. Each entry: `id`, `title`, `row`
  (`host` | `containers` | `portainer`), `unit` (`percent` | `bytes` | `bytes_per_s`
  | `count`), `source` (`prom` | `db`) and, for `prom`, the PromQL string.
  - host: CPU %, memory %, disk %, network in, network out (node-exporter)
  - containers: top 5 by CPU, top 5 by memory (cAdvisor, `name!=""`)
  - portainer: up/down per instance, stacks per instance, backup size per instance (db)
- `metrics/prometheus.py`: thin `httpx` client for `/api/v1/query_range` (httpx is
  already a dependency). Maps the matrix result to the series format.
- `metrics/ranges.py`: `1h|6h|24h|7d` → `(duration, step)`, e.g. 1h→15s, 6h→60s,
  24h→300s, 7d→1800s.
- `api/routes/metrics.py`, mounted in `api/router.py`:
  - `GET /api/metrics/panels` → catalog (no PromQL in the response).
  - `GET /api/metrics/range?panel=<id>&range=<r>` →
    `{panel, range, series: [{label, points: [[unix_ts, value], ...]}]}`.
    Unknown `panel` or `range` → 422. `db` panels read through `InfraRepository`.
- Errors: Prometheus down, timeout or non-success status → 502 with a generic
  message; the URL never appears in a response or log line that reaches a client.
  An empty result is `series: []`, not an error.

## Frontend (`frontend/src/`)

- `api/metrics.ts`: `getPanels()`, `getRange(panel, range)` via `apiGet`, owning the types.
- `stores/metrics.ts` (Pinia setup store): `range`, `refreshSeconds`, per-panel
  `{loading, error, series}`. Changing `range` refetches every panel; one panel's
  failure never touches the others.
- `components/MetricPanel.vue`: title, uPlot canvas, legend, inline error and
  "no data" states. Resizes with its grid cell.
- Dashboard view: header with range picker (1h/6h/24h/7d) and refresh selector
  (off/30s/1m); existing status cards stay on top, panel rows (host, containers,
  portainer) below. The timer is cleared on unmount and skips ticks while the tab
  is hidden.
- `package.json`: add `uplot`.

## Testing

- Backend unit: fake Prometheus client; catalog ids unique; range→step table; 422
  on unknown panel/range; 502 sanitized; `db` panels shaped correctly.
- Backend regression: pin the catalog and the OpenAPI snapshot.
- Frontend unit: store (range change refetches, failure isolation, refresh timer
  cleanup); `MetricPanel` states with uPlot mocked.
- No unit/regression test touches a real Prometheus.

## Deployment

`darkangel-api` must reach `prometheus` on a shared Docker network. Verify that the
service in `deploy/portainer-stack.yml` joins the network Prometheus is on, and
document `DARKANGEL_PROMETHEUS_URL` in `docs/dashboard.md`.
