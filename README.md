# MCP Playground

## What this is

MCP Playground is a browser-managed playground for authoring persistent mock MCP servers and OpenAI-compatible endpoints. It is purpose-built for Microsoft Copilot Studio demos, bootcamps, and solution-architecture training. Admins can model multiple mock servers, multiple endpoints, and shared demo datasets. Definitions and live demo data persist, so trainees can keep working across restarts.

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

- Admin web UI.
- API-key auth.
- Management MCP server at `/mcp/_admin`.
- OpenAI-compatible mock and proxy LLM endpoints.
- Export and import bundles.
- Traffic log.
- Azure deployment.
