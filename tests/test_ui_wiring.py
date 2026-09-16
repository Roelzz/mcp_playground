"""Static guards over the admin single-page app.

There is no browser in CI and no frontend test runner, so these read static/app.js
as text. They are deliberately narrow: each one pins a bug that actually reached
the trainer, rather than trying to type-check the whole file.

Two known false-positive traps, learned the hard way:
  * recipeRepeaterHtml() emits add/remove actions dynamically, so a naive
    "every data-action has a literal handler" diff reports phantom dead buttons.
  * The select-shaped actions (select-server, auto-traffic, select-recipe-tool-*)
    are wired through the change listener, not the click dispatch.
Both are accounted for below.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = STATIC / "app.js"
STYLE_CSS = STATIC / "style.css"
INDEX_HTML = STATIC / "index.html"


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


# --- wiring ----------------------------------------------------------------

# Handled by the change listener rather than the click dispatch.
CHANGE_LISTENER_ACTIONS = {
    "select-server",
    "auto-traffic",
    "select-recipe-tool-server",
    "select-recipe-tool-name",
}

# Handled inline by dedicated listeners, not the action dispatch.
SPECIAL_ACTIONS = {"close-modal"}


def test_every_rendered_action_has_a_handler(app_js: str) -> None:
    rendered = set(re.findall(r'data-action=\\"([a-z-]+)\\"', app_js))
    handled = set(re.findall(r'action === "([a-z-]+)"', app_js))
    orphans = rendered - handled - CHANGE_LISTENER_ACTIONS - SPECIAL_ACTIONS

    assert not orphans, f"buttons rendered with no handler: {sorted(orphans)}"


def test_every_handled_action_is_actually_rendered(app_js: str) -> None:
    rendered = set(re.findall(r'data-action=\\"([a-z-]+)\\"', app_js))
    # recipeRepeaterHtml() receives its add/remove action names as arguments and
    # renders them via esc(), so they never appear as a literal data-action="…".
    for call in re.findall(r"recipeRepeaterHtml\(([^;]*?)\)\s*\+", app_js):
        rendered |= set(re.findall(r'"((?:add|remove)-[a-z-]+)"', call))
    handled = set(re.findall(r'action === "([a-z-]+)"', app_js))
    orphans = handled - rendered - CHANGE_LISTENER_ACTIONS

    assert not orphans, f"handlers with nothing rendering them: {sorted(orphans)}"


def test_every_form_has_a_submit_branch(app_js: str) -> None:
    rendered = set(re.findall(r'data-form=\\"([a-z-]+)\\"', app_js))
    handled = set(re.findall(r'form\.dataset\.form === "([a-z-]+)"', app_js))
    handled |= set(re.findall(r'dataset\.form === "([a-z-]+)"', app_js))

    assert rendered - handled == set(), f"forms with no submit branch: {rendered - handled}"


# --- the upstream credential must not reach the browser --------------------


def test_upstream_key_is_never_a_plain_text_input(app_js: str) -> None:
    assert 'input("upstream_key"' not in app_js, (
        "upstream_key must render through secret(), which emits a password field; "
        "a plain input put the trainer's proxy credential on the projector"
    )
    assert 'secret("upstream_key"' in app_js


def test_the_ui_never_reads_a_raw_upstream_key_off_a_record(app_js: str) -> None:
    # The API only returns upstream_key_set now. Reading editing.upstream_key would
    # silently render an empty field and mask a regression on the backend.
    assert not re.search(r"editing\.upstream_key\b(?!_set)", app_js)


def test_secret_field_is_a_password_input_with_a_clear_control(app_js: str) -> None:
    secret_fn = re.search(r"function secret\(.*?\n  \}", app_js, re.S)
    assert secret_fn, "secret() helper is missing"
    body = secret_fn.group(0)
    assert 'type=\\"password\\"' in body
    assert "clear_upstream_key" in body


# --- feedback during the ~1s Azure Files round trips -----------------------


def test_tab_switching_renders_before_awaiting_data(app_js: str) -> None:
    handler = re.search(
        r'var tab = event\.target\.closest\("\[data-tab\]"\);.*?\n  \}\);', app_js, re.S
    )
    assert handler, "tab click handler not found"
    body = handler.group(0)
    before_await = body[: body.index("await")]
    assert "render();" in before_await, (
        "the tab must repaint before the first await, otherwise the click looks dead "
        "for the second or two a read costs on Azure Files"
    )
    assert "state.tab = previous" in body, "a failed load must not desync state.tab from the DOM"


def test_the_loading_flag_is_actually_used(app_js: str) -> None:
    assert app_js.count("state.loading") > 1, "state.loading was dead state for a long time"
    assert "load-bar" in app_js


def test_boot_shows_a_screen_before_loading_data(app_js: str) -> None:
    init = re.search(r"async function init\(\).*?\n  \}", app_js, re.S)
    assert init
    body = init.group(0)
    assert body.index("renderBoot") < body.index("await"), "init must paint before it awaits"


def test_auth_failure_and_data_failure_are_separate_screens(app_js: str) -> None:
    assert "function renderBootError" in app_js
    init = re.search(r"async function init\(\).*?\n  \}", app_js, re.S).group(0)
    # renderLogin must only be reachable from the auth check, not from the data load.
    assert init.count("renderLogin") == 1
    assert "bootData" in init


# --- error reporting -------------------------------------------------------


def test_validation_errors_are_unpacked_rather_than_stringified(app_js: str) -> None:
    assert "function errorText" in app_js, "FastAPI 422 detail is an array; it needs unpacking"
    fn = re.search(r"function errorText\(.*?\n  \}", app_js, re.S).group(0)
    assert "Array.isArray(detail)" in fn
    assert "item.msg" in fn


def test_errors_are_not_toasted_twice(app_js: str) -> None:
    assert "error.toasted" in app_js
    bare = re.findall(r'if \(err\.message !== "not authenticated"\) toast\(', app_js)
    assert not bare, "catch blocks must check err.toasted before toasting again"


# --- destructive actions ---------------------------------------------------


def test_confirm_modal_defaults_to_cancel_and_handles_escape(app_js: str) -> None:
    fn = re.search(r"function confirmModal\(.*?\n  \}\n", app_js, re.S)
    assert fn
    body = fn.group(0)
    assert "no.focus();" in body, "focus must start on Cancel, not on the destructive button"
    assert "yes.focus();" not in body
    assert '"Escape"' in body, "Escape must close the dialog"
    assert '"Tab"' in body, "focus must stay trapped inside the dialog"


def test_server_side_destructive_actions_are_confirmed(app_js: str) -> None:
    for handler in ("deleteItem", "deleteRecipe", "clearTraffic", "confirmPost"):
        fn = re.search(rf"(async )?function {handler}\(.*?\n  \}}", app_js, re.S)
        assert fn, f"{handler} not found"
        assert "confirmModal(" in fn.group(0), f"{handler} mutates the server without confirming"


def test_rules_editor_controls_respect_readonly(app_js: str) -> None:
    for action in ("add-rule", "rule-up", "rule-down", "remove-rule"):
        pattern = rf'data-action=\\"{action}\\"[^>]*?disabledIfReadonly\(\)'
        assert re.search(pattern, app_js), f"{action} is not gated for readonly principals"


def test_save_rules_is_disabled_until_the_endpoint_exists(app_js: str) -> None:
    fn = re.search(r"function rulesEditorHtml\(.*?\n  \}", app_js, re.S).group(0)
    assert "isNew" in fn, "a new endpoint has no id, so Save rules cannot work yet"


# --- copy that used to be wrong -------------------------------------------


def test_no_stale_product_name_remains(app_js: str) -> None:
    for path in (APP_JS, INDEX_HTML):
        text = path.read_text(encoding="utf-8")
        assert "Offline admin UI" not in text
        assert not re.search(r"(?<!Agent Integration )\bMCP Playground\b", text), (
            f"{path.name} still carries the pre-rename product name"
        )


def test_the_console_no_longer_claims_to_be_read_only(app_js: str) -> None:
    assert "Executor is admin-gated and read-only" not in app_js
    assert "READ_TOOL_TYPES" in app_js, "the console must warn before running a write tool"


def test_swagger_url_is_presented_as_admin_only(app_js: str) -> None:
    assert "Swagger export URL (admin-only)" in app_js
    assert "download-swagger" in app_js, "attendees get 401, so the trainer needs a download"


def test_read_tool_types_match_the_backend(app_js: str) -> None:
    import api

    declared = re.search(r"var READ_TOOL_TYPES = \[(.*?)\];", app_js).group(1)
    in_js = set(re.findall(r'"([a-z]+)"', declared))

    assert in_js == set(api.READ_TOOL_TYPES), "the UI's read-tool list drifted from api.py"


# --- styles the markup depends on -----------------------------------------


def test_classes_used_by_the_new_markup_exist(style_css: str) -> None:
    for cls in ("boot-screen", "boot-card", "spinner", "load-bar", "inline-check", "warn-inline"):
        assert f".{cls}" in style_css, f"markup renders .{cls} but the stylesheet has no rule"
