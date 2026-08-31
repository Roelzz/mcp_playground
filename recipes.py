"""Public read-only recipe pages."""

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from html import escape
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

import service
from auth import get_conn

router = APIRouter(tags=["public-recipes"])
Conn = Depends(get_conn)


def _h(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, str) and value.strip()]


def _tool_payload(tool: dict[str, Any]) -> dict[str, str]:
    return {
        "tool_name": str(tool.get("tool_name") or ""),
        "server_slug": str(tool.get("server_slug") or ""),
        "server_name": str(tool.get("server_name") or tool.get("server_slug") or ""),
    }


def _public_recipe(recipe: dict[str, Any], include_instructions: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "slug": str(recipe.get("slug") or ""),
        "title": str(recipe.get("title") or ""),
        "summary": str(recipe.get("summary") or ""),
        "department": str(recipe.get("department") or ""),
        "skill": str(recipe.get("skill") or ""),
        "example_prompts": _text_list(recipe.get("example_prompts")),
        "destinations": _text_list(recipe.get("destinations")),
        "tools": [_tool_payload(tool) for tool in recipe.get("tools", [])],
    }
    if include_instructions:
        payload["agent_instructions"] = str(recipe.get("agent_instructions") or "")
    return payload


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_h(title)} · MCP Playground</title>
  <script>
    (function () {{
      try {{
        var stored = localStorage.getItem("theme");
        var dark = stored
          ? stored === "dark"
          : window.matchMedia("(prefers-color-scheme: dark)").matches;
        if (dark) document.documentElement.setAttribute("data-theme", "dark");
      }} catch (err) {{ /* theme is best-effort */ }}
    }})();
  </script>
  <link rel="stylesheet" href="/ui/style.css">
</head>
<body class="public-body">
  <main class="public-shell">
    {body}
  </main>
</body>
</html>"""
    return HTMLResponse(html)


def _unique_servers(tools: Iterable[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    servers: list[str] = []
    for tool in tools:
        server = str(tool.get("server_name") or tool.get("server_slug") or "")
        if server and server not in seen:
            seen.add(server)
            servers.append(server)
    return servers


def _option(value: str, label: str, selected: str) -> str:
    selected_attr = " selected" if value == selected else ""
    return f'<option value="{_h(value)}"{selected_attr}>{_h(label)}</option>'


def _recipe_card(recipe: dict[str, Any]) -> str:
    tools = recipe["tools"]
    servers = _unique_servers(tools)
    server_chips = " ".join(f'<span class="badge">{_h(server)}</span>' for server in servers)
    prompt = recipe["example_prompts"][0] if recipe["example_prompts"] else "No example prompt yet."
    slug = quote(recipe["slug"], safe="")
    return f"""
      <article class="public-card">
        <h3>{_h(recipe["title"])}</h3>
        <p class="public-chips">
          <span class="badge skill-{_h(recipe["skill"])}">{_h(recipe["skill"])}</span>
          {server_chips or '<span class="badge">No tools</span>'}
        </p>
        <p class="public-summary">{_h(recipe["summary"])}</p>
        <p class="public-prompt-quote">{_h(prompt)}</p>
        <p class="public-actions">
          <a class="public-link" href="/r/{_h(slug)}">See the recipe →</a>
        </p>
      </article>
    """


@router.get("/api/public/recipes")
def public_recipes(conn: sqlite3.Connection = Conn) -> dict[str, list[dict[str, Any]]]:
    recipes = service.list_recipes(conn, published_only=True)
    return {"recipes": [_public_recipe(recipe) for recipe in recipes]}


@router.get("/r/", response_class=HTMLResponse, include_in_schema=False)
def recipe_index(
    skill: str = "",
    department: str = "",
    conn: sqlite3.Connection = Conn,
) -> HTMLResponse:
    recipes = [
        _public_recipe(recipe, include_instructions=False)
        for recipe in service.list_recipes(conn, published_only=True)
    ]
    skills = sorted({recipe["skill"] for recipe in recipes if recipe["skill"]})
    departments = sorted({recipe["department"] for recipe in recipes if recipe["department"]})
    visible = [
        recipe
        for recipe in recipes
        if (not skill or recipe["skill"] == skill)
        and (not department or recipe["department"] == department)
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recipe in visible:
        grouped[recipe["department"] or "General"].append(recipe)

    skill_options = [_option("", "All skills", skill)]
    skill_options.extend(_option(value, value.title(), skill) for value in skills)
    department_options = [_option("", "All departments", department)]
    department_options.extend(_option(value, value, department) for value in departments)
    groups = "".join(
        f"""
        <section class="public-group">
          <h2>{_h(name)} <span class="badge">{len(items)}</span></h2>
          <div class="public-grid">
            {''.join(_recipe_card(recipe) for recipe in items)}
          </div>
        </section>
        """
        for name, items in grouped.items()
    )
    if not groups:
        groups = (
            '<section class="public-group">'
            "<p>No published recipes match these filters.</p></section>"
        )

    body = f"""
      <header class="public-hero">
        <h1>Agent Playbook</h1>
        <p>Browse published trainer recipes for MCP-powered agents.</p>
        <form class="public-filters" method="get" action="/r/">
          <label>
            <span>Skill</span>
            <select name="skill">{''.join(skill_options)}</select>
          </label>
          <label>
            <span>Department</span>
            <select name="department">{''.join(department_options)}</select>
          </label>
          <button type="submit">Filter</button>
          <a class="public-link" href="/r/">Reset</a>
        </form>
      </header>
      {groups}
    """
    return _page("Agent Playbook", body)


@router.get("/r/{slug}", response_class=HTMLResponse, include_in_schema=False)
def recipe_detail(slug: str, conn: sqlite3.Connection = Conn) -> HTMLResponse:
    try:
        recipe = _public_recipe(service.get_recipe_by_slug(conn, slug, published_only=True))
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail="recipe not found") from exc

    tools = "".join(
        f"""
        <li class="public-row">
          <span class="badge">{_h(tool["server_name"] or tool["server_slug"])}</span>
          <code>{_h(tool["tool_name"])}</code>
        </li>
        """
        for tool in recipe["tools"]
    ) or '<li class="public-row">No MCP tools listed.</li>'
    prompts = "".join(
        f"""
        <li class="public-row" data-copy-block>
          <span data-copy-text>{_h(prompt)}</span>
          <button type="button" data-copy>Copy</button>
        </li>
        """
        for prompt in recipe["example_prompts"]
    ) or '<li class="public-row">No example prompts listed.</li>'
    destinations = "".join(
        f'<span class="badge">{_h(destination)}</span>' for destination in recipe["destinations"]
    )
    destinations_section = ""
    if destinations:
        destinations_section = f"""
        <section class="public-group">
          <h2>Destinations</h2>
          <p class="public-chips">{destinations}</p>
        </section>
        """

    body = f"""
      <p class="public-back"><a class="public-link" href="/r/">← Agent Playbook</a></p>
      <header class="public-hero">
        <h1>{_h(recipe["title"])}</h1>
        <p class="public-chips">
          <span class="badge skill-{_h(recipe["skill"])}">{_h(recipe["skill"])}</span>
          <span class="badge">{_h(recipe["department"] or "General")}</span>
        </p>
        <p class="public-summary">{_h(recipe["summary"])}</p>
        <div class="callout warn">
          Use the server URL your trainer gave you — this recipe lists the tools, not your team's
          endpoint.
        </div>
      </header>
      <section class="public-group" data-copy-block>
        <div class="public-group-head">
          <h2>Agent instructions</h2>
          <button type="button" data-copy>Copy</button>
        </div>
        <pre data-copy-text>{_h(recipe["agent_instructions"])}</pre>
      </section>
      <section class="public-group">
        <h2>Example prompts</h2>
        <ul class="public-list">{prompts}</ul>
      </section>
      <section class="public-group">
        <h2>MCP tools used</h2>
        <ul class="public-list">{tools}</ul>
      </section>
      {destinations_section}
      <script type="module">
        document.querySelectorAll("[data-copy]").forEach((button) => {{
          button.addEventListener("click", async () => {{
            const text = button.closest("[data-copy-block]")?.querySelector("[data-copy-text]")
              ?.textContent || "";
            await navigator.clipboard.writeText(text);
            const label = button.textContent;
            button.textContent = "Copied";
            setTimeout(() => {{ button.textContent = label; }}, 1200);
          }});
        }});
      </script>
    """
    return _page(recipe["title"], body)
