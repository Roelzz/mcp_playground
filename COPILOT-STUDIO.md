# Connecting MCP Playground to Copilot Studio

MCP Playground exposes every mock server through two independent surfaces. Pick the one that fits your situation:

| Path | Surface | When to use |
|------|---------|-------------|
| **A — MCP server** | `POST /mcp/{slug}` | Recommended. Copilot Studio discovers all tools automatically. Requires generative orchestration to be ON. |
| **B — Custom connector** | `GET /mock/{slug}/…` (plain REST) | Fallback. More steps, more control over which tools land in the agent. Use when MCP is blocked by a DLP policy. |

Both surfaces read the same datasets and enforce the same auth. Decide once, follow that path end to end.

---

## Bootcamp provisioning for trainers

Trainees do not log into MCP Playground. Trainers provision the mock servers first, then hand out URLs.

1. Build one good source server, or use one of the seeded sample servers.
2. Open the **Catalog** tab, the first tab in the sidebar.
3. Use **Clone** for one copy, or **Bulk clone** to create one server per team.
4. Open the **Cohort** tab, the second tab in the sidebar.
5. Filter to the team prefix, check readiness, then export the handout.

The Catalog cards show slug, auth mode, dataset/tool/row counts, and clone buttons. Bulk clone uses `prefix`, `count`, and optional `start`. Slugs are zero-padded to the width of `start + count - 1`, minimum width two, so prefix `hr-team`, count `20`, start `1` creates `hr-team01` through `hr-team20`.

Clone deep-copies the server, datasets, live rows, seed rows, and endpoints with remapped dataset IDs. It does not copy API keys, traffic, or LLM endpoints.

| Endpoint | Body | Result |
|----------|------|--------|
| `GET /api/catalog` | none | `200` bare JSON list. |
| `POST /api/servers/{server_id}/clone` | `{"slug": "...", "name": "..."}` where `name` is optional | `201` with the new server; `404` source missing, `409` slug taken, `400` invalid slug. |
| `POST /api/servers/{server_id}/bulk-clone` | `{"prefix": "...", "count": N, "start": 1}` where `start` defaults to `1` | `201` with `{"created": [...]}`. If any generated slug exists, it returns `409` and creates nothing. `count` must be `1` through `50`. |

### Cohort handouts

The Cohort tab has four panels:

| Panel | What it does |
|-------|--------------|
| Team servers | Shows slug, name, status, auth, dataset/endpoint/row summary, calls, last call, MCP URL, and REST URL with Copy buttons. `Ready` means seeded, in sync, and no auth. `Data drift` means live rows differ from seed rows. Seeded API-key servers show `Needs key`. |
| Handout preview | Print-friendly `Team slug | MCP URL | REST URL | Auth note` table. **Copy as Markdown**, **Download CSV**, and **Print view** honour the current filter. |
| Traffic by team | Shows calls, OK, errors, average duration, and last call per slug, sorted by call count descending. A missing team row means that team has not called anything yet. |
| Reset training environment | Destructive reset-to-seed. Use `Prefix filter` to target a cohort; empty prefix resets all servers after confirmation. |

Set `PUBLIC_BASE_URL` to the URL trainees can actually reach before exporting handouts. Cohort builds MCP, REST, and Swagger URLs from it. The default is `http://localhost:2009`, which produces handout links that only work on the trainer's machine.

| Endpoint | Result |
|----------|--------|
| `GET /api/cohort` | `200` bare list with `id`, `slug`, `name`, `description`, `auth_mode`, `dataset_count`, `endpoint_count`, `row_count`, `seed_count`, `seeded`, `in_sync`, `call_count`, `last_call_at`, `mcp_url`, `rest_url`, and `swagger_url`. |
| `GET /api/traffic/summary` | `200` per-slug traffic summary with `call_count`, `ok_count`, `error_count`, `avg_duration_ms`, and `last_call_at`. |
| `POST /api/servers/reset-all-to-seed` | Body is optional. Bodyless resets all servers; `{"prefix": "hr-team"}` resets matching slugs only. |

The admin management MCP server at `/mcp/_admin` exposes 41 tools. The bootcamp tools are `get_cohort`, `get_traffic_summary`, and `reset_all_to_seed`; `reset_all_to_seed` requires `confirm=true` or it returns `{"ok": false, "error": "set confirm=true to proceed", "would_affect": "..."}` and changes nothing.

---

## Seeded sample servers

First boot creates 5 servers, 15 datasets, 42 endpoints, 12 relationships, and 1 mock LLM endpoint. All five seeded servers use `auth_mode='none'`, so they are handout-ready without a key.

| Slug | Name | Datasets | Tools | Rows |
|------|------|----------|-------|------|
| `contoso-orders` | Contoso Orders | 2 | 7 | 160 |
| `northwind-hris` | Northwind HRIS | 3 | 9 | 64 |
| `fabrikam-it-service` | Fabrikam IT Service Desk | 3 | 8 | 56 |
| `adatum-crm` | Adatum Sales CRM | 3 | 9 | 76 |
| `contoso-expenses` | Contoso Travel Expenses | 4 | 9 | 116 |

Dataset row totals: `contoso-orders` has `orders` 40 / `order_lines` 120; `northwind-hris` has `employees` 30 / `time_off_requests` 28 / `org_units` 6; `fabrikam-it-service` has `tickets` 30 / `assets` 18 / `service_catalog` 8; `adatum-crm` has `accounts` 20 / `contacts` 30 / `opportunities` 26; `contoso-expenses` has `expense_reports` 24 / `expense_lines` 60 / `cost_centres` 8 / `approvals` 24.

---

## Before you connect anything: verify in the Test console

A misconfigured Copilot Studio agent is hard to debug. A misconfigured mock server is easy to debug — but only if you test it before leaving the playground.

1. In the MCP Playground UI, select your server from the top dropdown.
2. Click the **Test console** tab.
3. Pick a tool from the **Tool** dropdown. Each entry shows the tool name and the HTTP method + path it calls (e.g. `get_order — GET /orders/{id}`).
4. Keep **Surface** set to `REST — /mock/{slug}`.
5. Fill in the **Parameters JSON** field. For the seeded `contoso-orders` demo server, a minimal working call is:
   ```json
   { "id": 1001 }
   ```
6. Hit **Send**. The result panel shows the HTTP status badge, elapsed time, the raw response body, and a copyable `curl` command.

If the status is 200 and you see order data, the mock works. Only then proceed to Copilot Studio.

---

## Authentication

A server has one of two auth modes, set when the server is created:

| `auth_mode` | What the caller must send |
|-------------|--------------------------|
| `none` | Nothing. No credentials required. |
| `api_key` | An API key in either `X-API-Key: <key>` or `Authorization: Bearer <key>`. Both headers are accepted; `Bearer` is checked first. |

The seeded demo servers use `auth_mode: none`.

### Creating an API key

1. Open the **API keys** tab.
2. Click **Create key**, give it a label, and choose scope `readonly` (sufficient for demo purposes).
3. **Copy the key immediately.** It is shown exactly once. The key format is `mcpp_` followed by 43 URL-safe characters (48 characters in total).

The key belongs to the playground application, not to a specific server. One key works for any server on the instance that has `auth_mode: api_key`.

There is no per-team API key. The handout cannot contain one. No-auth servers are ready as-is; API-key servers need a global key distributed out-of-band.

---

## Path A — MCP server

### Step 1 — Collect the fields from the playground

1. Select your server in the top dropdown.
2. Click the **Connect it** tab.
3. The panel shows four copy buttons:

   | Field | Value for `contoso-orders` |
   |-------|---------------------------|
   | Server name | `Contoso Orders` |
   | Server description | *(the server's description text)* |
   | Server URL | `http://localhost:2009/mcp/contoso-orders` |
   | Authentication | `No authentication` |

   For a server with `auth_mode: api_key`, the Authentication field reads `API key`.

4. Copy each value. You'll paste them directly into Copilot Studio.

> **Two warnings are shown above the fields.** Take them seriously:
> - *Generative orchestration must be ON* — without it, the MCP tools are never invoked.
> - *DLP policy may block the connector* — if your tenant has a DLP policy that classifies custom connectors, a Power Platform admin may need to allow the endpoint before it works.

### Step 2 — Add the MCP server in Copilot Studio

1. Open your agent in Copilot Studio.
2. Go to **Tools**, then **Add a tool**, then **MCP server**.
3. Paste the fields you copied:
   - **Server name** → the name from the playground
   - **Server description** → the description from the playground
   - **Server URL** → `http://localhost:2009/mcp/contoso-orders`
4. For **Authentication**:
   - If `auth_mode: none` — select *No authentication*.
   - If `auth_mode: api_key` — select *API key* and paste the key you created in the API keys tab. Copilot Studio stores it and sends it as an `Authorization: Bearer` header.
5. Save. Copilot Studio calls the MCP endpoint and imports the tool list automatically.
6. Go to **Agent settings** and confirm **Generative orchestration** (sometimes labelled "Generative AI") is turned ON. This is required for MCP tools to be called.

### Step 3 — Verify

Test the agent with a prompt that should trigger one of the mock tools, for example:

> *Show me order 1001.*

The agent should call the `get_order` tool and return the seeded record. If it does not call any tool, generative orchestration is off. If it calls the tool but gets a 401, the API key is wrong or missing.

---

## Path B — custom connector via Swagger

> **Caveat:** The Swagger export and its structure have been verified by reading the source code. The full import-to-agent flow in Power Platform has **not** been tested end to end in a real tenant. The steps below describe what you need to do and what to look for; exact UI labels in Power Apps / Power Automate change between releases. If a label does not match, look for the equivalent option — the underlying operation is straightforward.

### Step 1 — Download the Swagger file

1. Select your server in the top dropdown.
2. Click the **Connect it** tab.
3. In the **Or connect it as a REST custom connector** section, find the **Swagger export** URL. It looks like:
   ```
   http://localhost:2009/api/servers/<id>/swagger
   ```
4. Open that URL in your browser, or append `?download=true` to force a file download named `{slug}-swagger.json`.

The downloaded file is a Swagger 2.0 document. Its `basePath` is `/mock/contoso-orders` and its `host` is derived from the `PUBLIC_BASE_URL` environment variable (default: `localhost:2009`). The `schemes` array matches the scheme of that URL (`http` for a local instance).

When `auth_mode: api_key`, the file contains:
```json
"securityDefinitions": {
  "api_key": {
    "type": "apiKey",
    "in": "header",
    "name": "X-API-Key"
  }
}
```
When `auth_mode: none`, there are no `securityDefinitions`.

### Step 2 — Create a custom connector in Power Platform

1. Go to [make.powerapps.com](https://make.powerapps.com) or [make.powerautomate.com](https://make.powerautomate.com).
2. Navigate to **Custom connectors** (the exact menu path varies — it is under the left nav or under **Data** depending on the release).
3. Choose **New custom connector** → **Import an OpenAPI file** (the option name may differ slightly; look for something that lets you upload a `.json` file).
4. Upload the Swagger file you downloaded.
5. Power Platform will show a multi-step wizard. Work through it:
   - **General**: host and base URL should be pre-filled from the Swagger. If the instance is not reachable from the internet, you may need to change the host here.
   - **Security**: for `auth_mode: none`, choose *No authentication*. For `auth_mode: api_key`, the Swagger declares `apiKey` in header `X-API-Key`; select the matching option and Power Platform will prompt for the key when the connector is used.
   - **Definition**: review the imported operations. Each endpoint in the playground becomes one action.
6. **Create** (or **Save**) the connector.

### Step 3 — Add the connector to your Copilot Studio agent

1. Open your agent in Copilot Studio.
2. Go to **Tools** → **Add a tool** → look for the custom connector you just created.
3. Select it and choose which actions to expose.
4. Provide the API key if prompted (for `api_key` servers).

### About the REST parameter contract

Every endpoint uses a **flat parameter dict**. Query parameters, path placeholders, and JSON body fields are all at the same level — there is no nesting. When you call `create_order`, the JSON body is a flat object:

```json
{
  "customer": "Fabrikam Facilities",
  "region": "West",
  "status": "open"
}
```

Status codes:
| Situation | HTTP status |
|-----------|-------------|
| Successful read / update / delete | `200` |
| Successful create | `201` |
| Record not found | `404` |
| Duplicate / conflict | `409` |
| Bad input | `400` |

Error responses always have the shape `{"detail": "<message>"}`.

---

### About `expand` and relationships

If a server declares relationships, every read operation gains an optional `expand` query parameter that inlines related rows into the response.

In the Swagger export, `expand` is a plain query-string parameter with `x-ms-summary: "Expand"`. **The response schema stays flat.** There is no `ExpandedItem` definition and no recursive `$ref`, because Power Platform's connector designer handles recursive schemas badly and would produce an unusable connector.

What that means in practice:

- Power Platform sees the base record shape. The expanded key arrives at runtime as an extra, untyped property.
- Dynamic content in Copilot Studio and Power Automate will **not** offer the expanded fields by name. Reference them with an expression against the raw response instead.
- If a flow needs typed access to the related record, call the related collection with its own operation rather than relying on `expand`.

Rules enforced by the server:

| Situation | Result |
|-----------|--------|
| `expand` on a list, search, or get | `200` with related rows inlined |
| `expand` on create, update, or delete | `400` `expand is not supported on <create\|update\|delete> operations` |
| Unknown expand name | `400` with the list of valid names |
| Dotted name such as `order.customer` | `400` — depth is one level only |
| More than 5 expand names | `400` |
| Repeated `?expand=x&expand=x` | `200`, deduplicated |

Example: `GET /mock/contoso-orders/order-lines?expand=order` inlines the parent order object into each line. `GET /mock/contoso-orders/orders/1001?expand=lines` inlines the array of lines into the order.

Relationships are advisory metadata, not database foreign keys. A missing parent inlines as `null`; a parent with no children inlines as `[]`.

---

## Troubleshooting

### 401 Unauthorized

The server has `auth_mode: api_key` and no valid key was sent, or the wrong key was sent.

- Check that you created a key in the **API keys** tab and copied it correctly. Keys start with `mcpp_`.
- For Path A (MCP): the key is stored in Copilot Studio's connector configuration. Re-enter it.
- For Path B (REST): the key must be configured in the custom connector's security settings.
- Both `X-API-Key: <key>` and `Authorization: Bearer <key>` are accepted. If your client sends the header under a different name, it will fail.

### 404 Not Found

Three different causes:

**Wrong slug.** The URL `http://localhost:2009/mock/wrong-slug/orders` returns:
```json
{"detail": "server 'wrong-slug' not found"}
```
Check the slug in the **Connect it** tab. It must match exactly, including case.

**Record ID not in the dataset.** The seeded `contoso-orders` demo server contains order IDs **1001 through 1040**. Calling `/orders/1` or `/orders/999` returns:
```json
{"detail": "Row with id='1' not found"}
```
Use IDs in the 1001–1040 range.

**Wrong HTTP method.** The `/mock/{slug}` surface accepts all five methods on one catch-all route and then matches method + path against the endpoint definitions. A method mismatch therefore returns **404, not 405**:
```json
{"detail": "no mock endpoint for POST /orders/1001"}
```
If you see a `no mock endpoint for …` message, check the method shown next to the path in the **Endpoints** tab.

### 405 Method Not Allowed

This does **not** come from `/mock/{slug}` — see the wrong-method case under 404 above. You will see 405 on the OpenAI-compatible LLM surface, where `/v1/{slug}/chat/completions` is `POST`-only and a `GET` returns 405.

### 406 Not Acceptable (MCP endpoint)

The MCP surface at `/mcp/{slug}` implements the MCP Streamable HTTP transport. A plain `GET` request without the correct `Accept` header returns 406. To call the MCP endpoint correctly, include:

```
Accept: application/json, text/event-stream
```

This is handled automatically by Copilot Studio's MCP client. You will only see 406 if you call the endpoint manually with a tool like `curl` and forget the header. The Test console in the playground uses the plain REST surface (`/mock/{slug}`), not the MCP surface, so it will not trigger this.
