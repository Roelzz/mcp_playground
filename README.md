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

If you hit that, point UV at a mirror without committing it as project config.

Host-side (`uv sync`, `uv run`):

```bash
export UV_DEFAULT_INDEX=https://<your-mirror>/simple
```

Container build:

```bash
podman build --build-arg UV_DEFAULT_INDEX=https://<your-mirror>/simple -t mcp-playground .
```

`uv.lock` records upstream PyPI URLs plus sha256 hashes. A mirror that copies the
pythonhosted layout serves identical artefacts, so the hashes still verify.

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
