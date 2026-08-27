# Connecting MCP Playground to Copilot Studio

MCP Playground exposes every mock server through two independent surfaces. Pick the one that fits your situation:

| Path | Surface | When to use |
|------|---------|-------------|
| **A — MCP server** | `POST /mcp/{slug}` | Recommended. Copilot Studio discovers all tools automatically. Requires generative orchestration to be ON. |
| **B — Custom connector** | `GET /mock/{slug}/…` (plain REST) | Fallback. More steps, more control over which tools land in the agent. Use when MCP is blocked by a DLP policy. |

Both surfaces read the same datasets and enforce the same auth. Decide once, follow that path end to end.

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

The seeded demo server `contoso-orders` uses `auth_mode: none`.

### Creating an API key

1. Open the **API keys** tab.
2. Click **Create key**, give it a label, and choose scope `readonly` (sufficient for demo purposes).
3. **Copy the key immediately.** It is shown exactly once. The key format is `mcpp_` followed by 43 URL-safe characters (48 characters in total).

The key belongs to the playground application, not to a specific server. One key works for any server on the instance that has `auth_mode: api_key`.

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
