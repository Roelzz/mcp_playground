# MCP Playground

## What this is

MCP Playground is a browser-managed playground for authoring persistent mock MCP servers and OpenAI-compatible endpoints. It is purpose-built for Microsoft Copilot Studio demos, bootcamps, and solution-architecture training. Admins can model multiple mock servers, multiple endpoints, and shared demo datasets. Definitions and live demo data persist, so trainees can keep working across restarts.

## Exposed surfaces

| Surface | Path | Auth |
|---|---|---|
| Admin UI | `/ui/` | session cookie |
| Admin CRUD API | `/api/*` | session cookie or API key; writes require `admin` scope |
| Mock MCP server | `/mcp/{slug}` | per-server (open or API key) |
| Management MCP server | `/mcp/_admin` | API key, `admin` scope |
| Plain REST mock | `/mock/{slug}/*` | per-server (open or API key) |
| OpenAI-compatible chat | `POST /v1/{slug}/chat/completions` | per-endpoint (open or API key) |
| Health | `/health` | open |

## Quick start (local, no container)

```bash
uv sync
cp .env.example .env
uv run python main.py
curl http://localhost:2009/health
```

Expected response:

```json
{"status":"ok"}
```

## Quick start (container)

```bash
docker compose up --build
```

Podman equivalent:

```bash
podman compose up --build
```

Then verify:

```bash
curl http://localhost:2009/health
```

## Configuration

Copy `.env.example` to `.env` and edit values for your environment.

| Key | Meaning | Default |
| --- | --- | --- |
| `LOG_LEVEL` | Loguru log level. | `INFO` |
| `HOST` | Host interface the FastAPI server binds to. Use `0.0.0.0` in containers. | `0.0.0.0` |
| `PORT` | Application port. Keep this at `2009`. | `2009` |
| `DB_PATH` | SQLite database file path. Containers override this to `/data/playground.db`. | `playground.db` |
| `PUBLIC_BASE_URL` | Public base URL used when generating links or callback URLs. | `http://localhost:2009` |
| `BOOTSTRAP_USER` | Optional initial admin username. Leave blank to auto-generate credentials on first boot. | blank |
| `BOOTSTRAP_PASSWORD` | Optional initial admin password. Leave blank to auto-generate credentials on first boot. | blank |
| `INSECURE_COOKIES` | Set to `1` when serving over plain HTTP on a LAN so session cookies still work. | `0` |

## Admin UI

The admin UI at `/ui/` has ten pages.

| Page | What it does |
|---|---|
| Catalog | Browse every server as cards with slug, auth mode, dataset/tool/row counts; clone one server or bulk-clone team copies. |
| Cohort | Bootcamp control panel for team readiness, handout export, traffic by team, and reset-to-seed. |
| Servers | Create and edit mock MCP servers; copy the live MCP URL for each. |
| Datasets | Paste or edit JSON or CSV; inspect the inferred field schema; manage seed snapshots. |
| Endpoints | Define MCP tools by describing REST-like endpoint shapes; tool type is inferred live. |
| LLM endpoints | Create OpenAI-compatible mock or proxy chat endpoints; set first-match-wins response rules. |
| Test console | Call an endpoint over **REST** (`/mock/{slug}/…`) to exercise writes and see the real HTTP status plus a copyable `curl` line, or through the **Executor** path (`/api/servers/{slug}/tools/{tool_name}/call`) which is admin-gated and read-only. |
| Traffic | Browse every recorded request and response; optionally auto-refresh every three seconds. |
| Connect it | Copy the exact values for the Copilot Studio MCP server dialog; also shows custom connector steps with a Swagger export link. |
| API keys | Create global API keys with `readonly` or `admin` scope; the plaintext key is shown once on creation. |

### Catalog and bootcamp cloning

The **Catalog** tab is the first tab in the sidebar. It shows every server in a card grid with slug, auth mode, and metrics for datasets, tools, and rows. From a card, an admin can create one clone or bulk-clone one source server into numbered team servers.

This is the bootcamp provisioning path: build one good server, then bulk-clone it once per training team.

| Endpoint | Body | Result |
|---|---|---|
| `GET /api/catalog` | none | `200` bare JSON list of servers with counts. |
| `POST /api/servers/{server_id}/clone` | `{"slug": "...", "name": "..."}` where `name` is optional | `201` with the new server; `404` if the source is missing, `409` if the slug exists, `400` if the slug is invalid. |
| `POST /api/servers/{server_id}/bulk-clone` | `{"prefix": "...", "count": N, "start": 1}` where `start` defaults to `1` | `201` with `{"created": [...]}`; `409` if any generated slug exists; `400` if `count` is below `1`, above `50`, or a generated slug is invalid. |

Bulk clone is all-or-nothing. If any target slug already exists, it creates nothing. Slugs are numbered with zero padding to the width of `start + count - 1`, with a minimum width of two: prefix `hr-team`, count `20`, start `1` creates `hr-team01` through `hr-team20`.

Clone deep-copies the server row, datasets, live dataset rows, seed dataset rows, and endpoints with remapped dataset IDs. It does not copy API keys, call log traffic, or LLM endpoints.

### Cohort control panel

The **Cohort** tab is the second tab in the sidebar. It is the bootcamp control panel for trainers.

| Panel | What it does |
|---|---|
| Team servers | One row per server with slug, name, readiness status, auth mode, dataset/endpoint/row summary, call count, last call, MCP URL, and REST URL. Each URL has a Copy button. A text filter narrows the list. `Ready` means seeded, in sync, and no auth. `Data drift` means live rows differ from seed rows. Seeded API-key servers show `Needs key`. |
| Handout preview | Print-friendly `Team slug | MCP URL | REST URL | Auth note` table. **Copy as Markdown**, **Download CSV**, and **Print view** all honour the current filter. |
| Traffic by team | Per-server calls, OK count, error count, average duration, and last call, sorted by call count descending. A server absent from this table has not called anything yet. |
| Reset training environment | Destructive reset panel. `Prefix filter` plus **Reset matching servers** behind a confirm dialog. Empty prefix resets all servers. Live rows are restored from seed rows. |

Set `PUBLIC_BASE_URL` before exporting handouts. Cohort URLs are built from `PUBLIC_BASE_URL`, defaulting to `http://localhost:2009`. If you leave the default, the handout contains `localhost` URLs that only work on the trainer's machine.

API keys are global, not per-server. The `api_key` table has no `server_id` column. Keys are sha256-hashed and the plaintext is shown exactly once at creation. The handout cannot contain a per-team API key. Servers with `auth_mode='none'` are handout-ready as-is; servers with `auth_mode='api_key'` need a key distributed out-of-band.

| Endpoint | Result |
|---|---|
| `GET /api/cohort` | `200` bare JSON list. Each item has `id`, `slug`, `name`, `description`, `auth_mode`, `dataset_count`, `endpoint_count`, `row_count`, `seed_count`, `seeded`, `in_sync`, `call_count`, `last_call_at`, `mcp_url`, `rest_url`, and `swagger_url`. |
| `GET /api/traffic/summary` | `200` bare JSON list grouped by `target_slug`, with `call_count`, `ok_count`, `error_count`, `avg_duration_ms`, and `last_call_at`, ordered by call count descending. |
| `POST /api/servers/reset-all-to-seed` | `200` reset summary. Body is optional; bodyless resets all servers. Use `{"prefix": "hr-team"}` to reset only matching slugs. |

## Seeded sample servers

First boot seeds 5 servers, 15 datasets, 42 endpoints, and 1 mock LLM endpoint. All five seeded servers use `auth_mode='none'`, so they are handout-ready without a key.

| Slug | Name | Datasets | Tools | Rows |
|---|---|---|---|---|
| `contoso-orders` | Contoso Orders | 2 | 7 | 160 |
| `northwind-hris` | Northwind HRIS | 3 | 9 | 64 |
| `fabrikam-it-service` | Fabrikam IT Service Desk | 3 | 8 | 56 |
| `adatum-crm` | Datum Sales CRM | 3 | 9 | 76 |
| `contoso-expenses` | Contoso Travel Expenses | 4 | 9 | 116 |

Dataset row totals: `contoso-orders` has `orders` 40 / `order_lines` 120; `northwind-hris` has `employees` 30 / `time_off_requests` 28 / `org_units` 6; `fabrikam-it-service` has `tickets` 30 / `assets` 18 / `service_catalog` 8; `adatum-crm` has `accounts` 20 / `contacts` 30 / `opportunities` 26; `contoso-expenses` has `expense_reports` 24 / `expense_lines` 60 / `cost_centres` 8 / `approvals` 24.

## Calling a server over plain REST

Every server is exposed twice from the same endpoint definitions.

| Surface | URL | Use it for |
| --- | --- | --- |
| MCP | `/mcp/{slug}` | Copilot Studio MCP onboarding wizard |
| REST | `/mock/{slug}` | Custom connectors, Postman, curl, any HTTP client |

The REST surface uses the endpoint's own method and path verbatim.

```bash
curl http://localhost:2009/mock/contoso-orders/orders?limit=5
curl http://localhost:2009/mock/contoso-orders/orders/1001
curl "http://localhost:2009/mock/contoso-orders/orders?status=open"
curl "http://localhost:2009/mock/contoso-orders/orders/search?q=northwind"
curl -X POST http://localhost:2009/mock/contoso-orders/orders \
  -H 'content-type: application/json' \
  -d '{"customer":"Fabrikam","status":"open","total":42}'
curl -X DELETE http://localhost:2009/mock/contoso-orders/orders/1001
```

Query string, JSON body and path placeholders are merged into one flat parameter set. Create returns `201`, everything else returns `200`.

The **Test console** in the admin UI calls this surface directly. Pick `REST — /mock/{slug}` as the surface to exercise writes and see the real HTTP status plus a copyable `curl` line. The `Executor` surface is admin-gated and read-only, so it cannot test create, update or delete.

When a server's authentication is set to API key, send the key as `X-API-Key: <key>` or `Authorization: Bearer <key>`.

### Importing into Power Platform as a custom connector

The Swagger export at `/api/servers/{id}/swagger` describes this REST surface, so it imports directly as a custom connector.

1. Download the Swagger from the server's **Connect it** tab.
2. Open Power Apps or Power Automate, go to **Custom connectors**.
3. Choose **New custom connector > Import an OpenAPI file**.
4. Add the connector to your agent in Copilot Studio.

See **[COPILOT-STUDIO.md](COPILOT-STUDIO.md)** for the full runbook covering both connection paths — MCP server onboarding and Swagger-based custom connector.

## Management MCP server

`/mcp/_admin` is a second FastMCP server that exposes **36 tools** mirroring the admin REST API. It lets an LLM agent drive the entire playground — create servers, clone team servers, load datasets, define endpoints, adjust LLM responses, and inspect traffic — without a human touching the web UI.

Authentication: API key with `admin` scope, passed as `X-API-Key: <key>` or `Authorization: Bearer <key>`.

Tools are grouped into six areas:

| Area | Tools |
|---|---|
| Servers | `list_servers`, `get_server`, `create_server`, `clone_server`, `bulk_clone_server`, `get_catalog`, `update_server`, `delete_server`, `get_connection_info` |
| Datasets | `list_datasets`, `get_dataset`, `create_dataset`, `update_dataset`, `delete_dataset` |
| Dataset rows | `list_rows`, `replace_rows`, `add_rows`, `reset_to_seed`, `reset_all_to_seed`, `save_as_seed` |
| Endpoints | `list_endpoints`, `get_endpoint`, `create_endpoint`, `update_endpoint`, `delete_endpoint` |
| LLM endpoints | `list_llm_endpoints`, `get_llm_endpoint`, `create_llm_endpoint`, `update_llm_endpoint`, `delete_llm_endpoint`, `set_llm_responses` |
| Misc | `call_tool`, `get_cohort`, `get_traffic`, `get_traffic_summary`, `clear_traffic` |

`reset_all_to_seed` requires `confirm=true`. Without it, the tool returns `{"ok": false, "error": "set confirm=true to proceed", "would_affect": "..."}` and changes nothing.

## Data & persistence

MCP Playground has three data tiers.

Definitions are the authored servers, endpoints, and datasets. They persist forever until an admin changes them.

Live data is the mutable runtime state used during a demo. It persists across restarts and is mutated by agent calls. If an agent creates an order during a demo, that order is still there tomorrow.

Seed data is an immutable snapshot. Reset to seed restores live data back to the snapshot. Save as seed promotes the current live data into the snapshot.

The Compose setup uses the named Docker volume `playground-data` mounted at `/data`. That is what keeps SQLite data alive across `docker compose restart`, `docker compose down`, and later `docker compose up`. Deleting that volume wipes all definitions, live data, and seed data.

## Development

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format .
```

## Network note: blocked PyPI downloads

The project uses the public PyPI index. On some corporate networks — including any
machine behind Microsoft Entra Global Secure Access — `files.pythonhosted.org` is
blocked by web filtering, so `pypi.org` resolves but package downloads fail. The block
applies inside containers too.

If you hit that, redirect UV to a mirror locally — never commit the mirror as project
config.

Host-side dependency resolution (`uv lock`):

```bash
export UV_DEFAULT_INDEX=https://<your-mirror>/simple
```

Container build. `uv sync --frozen` uses the absolute artefact URLs recorded in
`uv.lock`, so `UV_DEFAULT_INDEX` alone is not enough — rewrite the lock, build, restore:

```bash
sed -i '' -e 's|https://files.pythonhosted.org/packages|https://<your-mirror>/packages|g' \
          -e 's|https://pypi.org/simple|https://<your-mirror>/simple|g' uv.lock
podman build -t mcp-playground .
git checkout uv.lock
```

`uv.lock` records sha256 hashes alongside every URL. A mirror that copies the
pythonhosted path layout serves identical artefacts, so the hashes still verify and the
rewrite is safe.

For a production or Microsoft-shop deployment, use an Azure Artifacts feed with a PyPI
upstream instead of a public mirror.

## Roadmap

- Azure deployment.
