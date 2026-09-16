(function () {
  "use strict";

  var TABS = [
    ["catalog", "Catalog"],
    ["recipes", "Recipes"],
    ["cohort", "Cohort"],
    ["servers", "Servers"],
    ["datasets", "Datasets"],
    ["endpoints", "Endpoints"],
    ["llm", "LLM endpoints"],
    ["test", "Test console"],
    ["traffic", "Traffic"],
    ["connect", "Connect it"],
    ["keys", "API keys"]
  ];

  // Mirrors READ_TOOL_TYPES in api.py; anything outside this set mutates stored data.
  var READ_TOOL_TYPES = ["list", "get", "search"];

  var state = {
    user: null,
    tab: "catalog",
    loading: false,
    servers: [],
    catalog: [],
    cohort: [],
    cohortError: null,
    cohortTrafficSummary: [],
    cohortTrafficError: null,
    cohortFilter: "",
    cohortResetting: false,
    selectedServerId: null,
    datasets: [],
    selectedDatasetId: null,
    datasetDetails: {},
    datasetRows: {},
    recipes: [],
    recipeDetails: {},
    selectedRecipeId: null,
    recipeDraft: null,
    recipeEndpointDetails: {},
    recipeToolServerId: null,
    recipeToolName: "",
    endpoints: [],
    llms: [],
    selectedLlmId: null,
    llmDetails: {},
    llmRules: [],
    keys: [],
    newKey: null,
    traffic: [],
    expandedTraffic: null,
    autoTraffic: false,
    trafficTimer: null,
    testResult: null,
    testElapsed: null,
    testMode: "rest",
    testTool: null,
    testSearch: "",
    testParams: "{}",
    testStatus: null,
    testCurl: null,
    testApiKey: "",
    connectPlatform: "copilot",
    editingServerId: null,
    editingDatasetId: null,
    editingEndpointId: null,
    editingLlmId: null,
    relationships: [],
    validateResult: null
  };

  var app = document.getElementById("app");
  var toastRoot = document.getElementById("toast-root");
  var modalRoot = document.getElementById("modal-root");

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function pretty(value) {
    return JSON.stringify(value == null ? null : value, null, 2);
  }

  function canWrite() {
    return state.user && state.user.scope === "admin";
  }

  function disabledIfReadonly() {
    return canWrite() ? "" : " disabled title=\"Readonly users cannot mutate data\"";
  }

  function selectedServer() {
    return state.servers.find(function (s) { return String(s.id) === String(state.selectedServerId); }) || state.servers[0] || null;
  }

  function selectedDataset() {
    return state.datasets.find(function (d) { return String(d.id) === String(state.selectedDatasetId); }) || state.datasets[0] || null;
  }

  function selectedRecipe() {
    if (!state.selectedRecipeId) return null;
    return state.recipes.find(function (r) { return String(r.id) === String(state.selectedRecipeId); }) || null;
  }

  function selectedLlm() {
    return state.llms.find(function (l) { return String(l.id) === String(state.selectedLlmId); }) || state.llms[0] || null;
  }

  function endpointUrl(server) {
    return server ? window.location.origin + "/mcp/" + server.slug : "";
  }

  function restBaseUrl(server) {
    return server ? window.location.origin + "/mock/" + server.slug : "";
  }

  function restUrl(server, endpoint) {
    return server && endpoint ? restBaseUrl(server) + endpoint.path : "";
  }

  function llmUrl(llm) {
    return llm ? window.location.origin + "/v1/" + llm.slug + "/chat/completions" : "";
  }

  function recipeUrl(slug) {
    return window.location.origin + "/r/" + (slug || "");
  }

  function toast(message, type) {
    var node = document.createElement("div");
    node.className = "toast " + (type || "");
    node.textContent = typeof message === "string" ? message : String(message);
    toastRoot.appendChild(node);
    setTimeout(function () { node.remove(); }, 5200);
  }

  function setBusy(button, busy) {
    if (!button) return;
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.textContent = "Working…";
      button.disabled = true;
    } else {
      button.textContent = button.dataset.originalText || button.textContent;
      button.disabled = false;
    }
  }

  function confirmModal(title, message, actionText) {
    return new Promise(function (resolve) {
      var previouslyFocused = document.activeElement;
      // Cancel comes first in the DOM and takes focus: this dialog only ever guards
      // destructive actions, so a stray Enter must not be the one that deletes.
      modalRoot.innerHTML = "<div class=\"modal-backdrop\" role=\"presentation\"><section class=\"modal\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"modal-title\"><h2 id=\"modal-title\">" + esc(title) + "</h2><p>" + esc(message) + "</p><div class=\"actions\"><button class=\"btn\" data-modal=\"no\">Cancel</button><button class=\"btn danger\" data-modal=\"yes\">" + esc(actionText || "Delete") + "</button></div></section></div>";
      var yes = modalRoot.querySelector("[data-modal='yes']");
      var no = modalRoot.querySelector("[data-modal='no']");
      no.focus();

      function onKeydown(event) {
        if (event.key === "Escape") {
          event.preventDefault();
          close(false);
          return;
        }
        if (event.key !== "Tab") return;
        // Keep focus inside the dialog; there are exactly two focusable controls.
        event.preventDefault();
        (document.activeElement === no ? yes : no).focus();
      }

      function close(value) {
        document.removeEventListener("keydown", onKeydown, true);
        modalRoot.innerHTML = "";
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        resolve(value);
      }

      document.addEventListener("keydown", onKeydown, true);
      yes.addEventListener("click", function () { close(true); });
      no.addEventListener("click", function () { close(false); });
      modalRoot.querySelector(".modal-backdrop").addEventListener("click", function (event) {
        if (event.target.className === "modal-backdrop") close(false);
      });
    });
  }

  function openCloneModal(server) {
    if (!server) return;
    modalRoot.innerHTML = "<div class=\"modal-backdrop\" role=\"presentation\"><section class=\"modal catalog-modal\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"clone-title\">" +
      "<div class=\"panel-heading\"><div><h2 id=\"clone-title\">Clone " + esc(server.name) + "</h2><p>Create one live copy of <code>" + esc(server.slug) + "</code>.</p></div></div>" +
      "<form class=\"form-grid\" data-form=\"clone-server\">" + hidden("source_id", server.id) +
      "<div class=\"error-inline\" data-clone-error hidden></div>" +
      input("clone_slug", "New slug", "", "hr-team01") + input("clone_name", "New name (optional)", "", server.name) +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Create clone</button><button class=\"btn\" type=\"button\" data-action=\"close-modal\">Cancel</button></div></form></section></div>";
  }

  function openBulkCloneModal(server) {
    if (!server) return;
    modalRoot.innerHTML = "<div class=\"modal-backdrop\" role=\"presentation\"><section class=\"modal catalog-modal catalog-modal-wide\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"bulk-clone-title\">" +
      "<div class=\"panel-heading\"><div><h2 id=\"bulk-clone-title\">Bulk clone " + esc(server.name) + "</h2><p>Generate per-team live copies from <code>" + esc(server.slug) + "</code>.</p></div></div>" +
      "<form class=\"form-grid\" data-form=\"bulk-clone-server\">" + hidden("source_id", server.id) +
      "<div class=\"callout info\"><strong>Atomic:</strong> if any target slug already exists, the backend returns 409 and nothing is created.</div>" +
      "<div class=\"error-inline\" data-clone-error hidden></div>" +
      "<div class=\"inline-grid\"><div class=\"form-row\"><label for=\"bulk_prefix\">Prefix</label><input id=\"bulk_prefix\" name=\"prefix\" value=\"\" placeholder=\"hr-team\" data-bulk-clone-field></div>" +
      "<div class=\"form-row\"><label for=\"bulk_count\">Count</label><input id=\"bulk_count\" name=\"count\" type=\"number\" min=\"1\" max=\"50\" value=\"20\" data-bulk-clone-field></div></div>" +
      "<div class=\"form-row\"><label for=\"bulk_start\">Start</label><input id=\"bulk_start\" name=\"start\" type=\"number\" min=\"1\" value=\"1\" data-bulk-clone-field></div>" +
      "<div class=\"form-row\"><label>Slug preview</label><div class=\"slug-preview\" data-bulk-clone-preview></div></div>" +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Create clones</button><button class=\"btn\" type=\"button\" data-action=\"close-modal\">Cancel</button></div></form></section></div>";
    updateBulkClonePreview();
  }

  function generatedBulkCloneSlugs(prefix, count, start) {
    count = Number.parseInt(count, 10);
    start = Number.parseInt(start || 1, 10);
    if (!prefix || !Number.isFinite(count) || !Number.isFinite(start) || count < 1 || start < 1) return [];
    var end = start + count - 1;
    var width = Math.max(2, String(end).length);
    var slugs = [];
    for (var i = 0; i < count; i += 1) {
      slugs.push(prefix + String(start + i).padStart(width, "0"));
    }
    return slugs;
  }

  function updateBulkClonePreview() {
    var form = modalRoot.querySelector("form[data-form='bulk-clone-server']");
    var preview = modalRoot.querySelector("[data-bulk-clone-preview]");
    if (!form || !preview) return;
    var slugs = generatedBulkCloneSlugs(form.elements.prefix.value.trim(), form.elements.count.value, form.elements.start.value);
    preview.innerHTML = slugs.length ? slugs.map(function (slug) { return "<code>" + esc(slug) + "</code>"; }).join("<span>, </span>") : "<span class=\"help\">Enter a prefix and count to preview target slugs.</span>";
  }

  function catalogServerById(id) {
    return state.catalog.find(function (s) { return String(s.id) === String(id); }) || state.servers.find(function (s) { return String(s.id) === String(id); }) || null;
  }

  function showCloneError(form, message) {
    var node = form.querySelector("[data-clone-error]");
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
  }

  function cloneErrorMessage(err, bulk) {
    if (err.status === 409) return bulk ? "One or more target slugs already exists. Nothing was created." : "That slug is already taken.";
    if (err.status === 400) return bulk ? "Invalid bulk-clone settings. Count must be 1–50 and the prefix/start must be valid." : "Invalid slug. Use a unique URL-safe slug.";
    if (err.status === 404) return "Source server was not found.";
    return err.message || "Clone failed.";
  }

  async function api(path, options) {
    var opts = Object.assign({}, options || {});
    var silentError = Boolean(opts.silentError);
    delete opts.silentError;
    opts.credentials = "same-origin";
    opts.headers = opts.headers || {};
    if (opts.body && !opts.headers["Content-Type"]) opts.headers["Content-Type"] = "application/json";
    var response;
    try {
      response = await fetch(path, opts);
    } catch (err) {
      toast("Network error: " + err.message, "error");
      err.toasted = true;
      throw err;
    }
    if (response.status === 401 && path !== "/api/auth/me" && path !== "/api/auth/login") {
      state.user = null;
      renderLogin("Session expired. Sign in again.");
      throw new Error("not authenticated");
    }
    var data = null;
    if (response.status !== 204) {
      var text = await response.text();
      if (text) {
        try { data = JSON.parse(text); } catch (_err) { data = { detail: text }; }
      }
    }
    if (!response.ok) {
      var detail = errorText(data, response);
      if (response.status === 429) detail = "Too many attempts. Wait a minute and try again.";
      if (!silentError) toast(detail, "error");
      var error = new Error(detail);
      error.status = response.status;
      error.data = data;
      error.toasted = !silentError;
      throw error;
    }
    return data;
  }

  // FastAPI returns a 422 detail as an array of {loc, msg} objects. Handing that
  // straight to toast() printed "[object Object]" and told the trainer nothing.
  function errorText(data, response) {
    var detail = data && (data.detail != null ? data.detail : data.message);
    if (detail == null || detail === "") return response.statusText || "Request failed.";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      var lines = detail.map(function (item) {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return String(item);
        var loc = Array.isArray(item.loc)
          ? item.loc.filter(function (part) { return part !== "body" && part !== "query"; }).join(".")
          : "";
        var msg = item.msg || item.message || "is invalid";
        return loc ? loc + ": " + msg : msg;
      }).filter(Boolean);
      if (lines.length) return lines.join(" · ");
    }
    try { return JSON.stringify(detail); } catch (_err) { return response.statusText || "Request failed."; }
  }

  async function init() {
    renderBoot("Loading the playground…");
    try {
      state.user = await api("/api/auth/me", { silentError: true });
    } catch (_err) {
      renderLogin();
      return;
    }
    await bootData();
  }

  // An authentication failure and a data-load failure need different screens. They
  // used to share one catch, so a 500 on /api/servers dropped the trainer back to the
  // login form with a perfectly valid session — and signing in again did nothing.
  async function bootData() {
    renderBoot("Loading your servers and datasets…");
    try {
      await loadInitial();
    } catch (err) {
      if (err.message === "not authenticated") return;
      renderBootError(err.message || "The server did not respond.");
      return;
    }
    render();
  }

  function renderBoot(message) {
    app.innerHTML = "<main class=\"boot-screen\"><div class=\"boot-card\"><div class=\"spinner\"></div><p>" + esc(message) + "</p></div></main>";
  }

  function renderBootError(message) {
    app.innerHTML = "<main class=\"boot-screen\"><div class=\"boot-card\"><h1>Could not load the playground</h1>" +
      "<p class=\"help\">" + esc(message) + "</p>" +
      "<p class=\"help\">Your session is still valid. This is a server or network problem, not a sign-in problem.</p>" +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"button\" data-action=\"retry-boot\">Try again</button></div></div></main>";
  }

  async function loadInitial() {
    var initial = await Promise.all([api("/api/servers"), api("/api/catalog")]);
    state.servers = initial[0];
    state.catalog = initial[1] || [];
    if (!state.selectedServerId && state.servers.length) state.selectedServerId = state.servers[0].id;
    await Promise.all([loadDatasets(), loadEndpoints(), loadLlms(), loadRelationships(), loadRecipes()]);
  }

  async function loadCatalog() {
    state.catalog = await api("/api/catalog") || [];
  }

  async function loadCohort() {
    try {
      state.cohort = await api("/api/cohort", { silentError: true }) || [];
      state.cohortError = null;
    } catch (err) {
      state.cohort = [];
      state.cohortError = err.message || "Cohort endpoint unavailable.";
    }
  }

  async function loadCohortTrafficSummary() {
    try {
      state.cohortTrafficSummary = await api("/api/traffic/summary", { silentError: true }) || [];
      state.cohortTrafficError = null;
    } catch (err) {
      state.cohortTrafficSummary = [];
      state.cohortTrafficError = err.message || "Traffic summary endpoint unavailable.";
    }
  }

  async function loadCohortPage() {
    await Promise.all([loadCohort(), loadCohortTrafficSummary()]);
  }

  async function loadServers() {
    state.servers = await api("/api/servers");
    if (state.servers.length && !selectedServer()) state.selectedServerId = state.servers[0].id;
  }

  async function loadDatasets() {
    var server = selectedServer();
    state.datasets = server ? await api("/api/servers/" + server.id + "/datasets") : [];
    if (state.datasets.length && !selectedDataset()) state.selectedDatasetId = state.datasets[0].id;
    if (selectedDataset()) await loadDatasetDetail(selectedDataset().id);
  }

  async function loadDatasetDetail(id) {
    if (!id) return;
    var detail = await api("/api/datasets/" + id);
    var rows = await api("/api/datasets/" + id + "/rows");
    state.datasetDetails[id] = detail;
    state.datasetRows[id] = rows;
  }

  async function loadRelationships() {
    var server = selectedServer();
    state.relationships = server ? await api("/api/servers/" + server.id + "/relationships") : [];
    state.validateResult = null;
  }

  async function loadEndpoints() {
    var server = selectedServer();
    state.endpoints = server ? await api("/api/servers/" + server.id + "/endpoints") : [];
  }

  async function loadRecipes() {
    state.recipes = await api("/api/recipes") || [];
    if (state.selectedRecipeId && !state.recipes.some(function (r) { return String(r.id) === String(state.selectedRecipeId); })) {
      state.selectedRecipeId = null;
      state.recipeDraft = null;
    }
    if (!state.selectedRecipeId && state.recipes.length && !state.recipeDraft) state.selectedRecipeId = state.recipes[0].id;
    if (state.selectedRecipeId) await loadRecipeDetail(state.selectedRecipeId);
    else if (!state.recipeDraft) state.recipeDraft = emptyRecipeDraft();
    await ensureRecipeToolEndpoints();
  }

  async function loadRecipeDetail(id) {
    if (!id) return;
    var detail = await api("/api/recipes/" + id);
    state.recipeDetails[id] = detail;
    state.recipeDraft = recipeToDraft(detail);
  }

  async function loadRecipeEndpointsForServer(serverId) {
    if (!serverId || state.recipeEndpointDetails[serverId]) return;
    state.recipeEndpointDetails[serverId] = await api("/api/servers/" + serverId + "/endpoints");
  }

  async function ensureRecipeToolEndpoints() {
    var serverId = state.recipeToolServerId || (state.servers[0] && state.servers[0].id);
    if (!serverId) return;
    state.recipeToolServerId = Number(serverId);
    await loadRecipeEndpointsForServer(state.recipeToolServerId);
    var endpoints = state.recipeEndpointDetails[state.recipeToolServerId] || [];
    if (!endpoints.some(function (e) { return e.tool_name === state.recipeToolName; })) state.recipeToolName = endpoints[0] ? endpoints[0].tool_name : "";
  }

  async function loadLlms() {
    state.llms = await api("/api/llm-endpoints");
    if (state.llms.length && !selectedLlm()) state.selectedLlmId = state.llms[0].id;
  }

  async function loadLlmDetail(id) {
    if (!id) return;
    var detail = await api("/api/llm-endpoints/" + id);
    state.llmDetails[id] = detail;
    state.llmRules = (detail.responses || []).slice().sort(function (a, b) { return (a.ordinal || 0) - (b.ordinal || 0); }).map(function (r) {
      return { match_type: r.match_type || "always", match_value: r.match_value || "", response: r.response || "" };
    });
  }

  function renderLogin(message) {
    app.innerHTML = "<main class=\"login-screen\"><form class=\"login-card\" data-form=\"login\"><h1>Agent Integration Playground</h1><p>Admin access required.</p>" +
      (message ? "<div class=\"error-inline\">" + esc(message) + "</div>" : "") +
      "<div class=\"form-grid\"><div class=\"form-row\"><label for=\"username\">Username</label><input id=\"username\" name=\"username\" autocomplete=\"username\" required></div>" +
      "<div class=\"form-row\"><label for=\"password\">Password</label><input id=\"password\" name=\"password\" type=\"password\" autocomplete=\"current-password\" required></div>" +
      "<button class=\"btn primary full\" type=\"submit\">Sign in</button></div></form></main>";
  }

  // selectedServer() and selectedDataset() quietly fall back to the first record, so a
  // deleted id kept the dropdown showing nothing selected while the app operated on a
  // different server. Pin the stored ids to whatever those getters actually resolved to,
  // and drop edit targets that no longer exist.
  function syncSelections() {
    var server = selectedServer();
    state.selectedServerId = server ? server.id : null;
    var dataset = selectedDataset();
    state.selectedDatasetId = dataset ? dataset.id : null;
    if (state.editingEndpointId != null && !state.endpoints.some(function (e) { return e.id === state.editingEndpointId; })) {
      state.editingEndpointId = null;
    }
    if (state.editingLlmId != null && !state.llms.some(function (l) { return l.id === state.editingLlmId; })) {
      state.editingLlmId = null;
      state.llmRules = [];
    }
    if (state.testTool && !state.endpoints.some(function (e) { return e.tool_name === state.testTool; })) {
      state.testTool = null;
    }
  }

  function render() {
    if (!state.user) return renderLogin();
    if (!state.loading) syncSelections();
    app.innerHTML = "<div class=\"top-warning\">live endpoint · synthetic data · writes persist — use Reset to seed</div>" +
      (state.loading ? "<div class=\"load-bar\" role=\"status\" aria-label=\"Loading\"></div>" : "") +
      "<div class=\"app-layout" + (state.loading ? " is-loading" : "") + "\"><aside class=\"sidebar\"><div class=\"brand\"><div class=\"brand-mark\">A</div><div><div class=\"brand-title\">Agent Integration Playground</div><div class=\"brand-subtitle\">Trainer admin</div></div></div>" +
      navHtml() + userHtml() + "</aside><main class=\"main\">" + pageHtml() + "</main></div>";
    afterRender();
  }

  function navHtml() {
    return "<nav class=\"nav\" aria-label=\"Admin sections\">" + TABS.map(function (tab) {
      return "<button type=\"button\" class=\"" + (state.tab === tab[0] ? "active" : "") + "\" data-tab=\"" + tab[0] + "\">" + esc(tab[1]) + "</button>";
    }).join("") + "</nav>";
  }

  function userHtml() {
    return "<div class=\"user-panel\"><div class=\"user-line\">Signed in as <strong>" + esc(state.user.username) + "</strong> <span class=\"scope-badge\">" + esc(state.user.scope) + "</span></div>" +
      (!canWrite() ? "<div class=\"readonly-note\">Readonly: create, edit and delete controls are disabled.</div>" : "") +
      "<button class=\"btn ghost\" type=\"button\" data-action=\"logout\">Logout</button></div>";
  }

  function pageHtml() {
    if (state.tab === "catalog") return renderCatalog();
    if (state.tab === "recipes") return renderRecipes();
    if (state.tab === "cohort") return renderCohort();
    if (state.tab === "servers") return renderServers();
    if (state.tab === "datasets") return renderDatasets();
    if (state.tab === "endpoints") return renderEndpoints();
    if (state.tab === "llm") return renderLlm();
    if (state.tab === "test") return renderTestConsole();
    if (state.tab === "traffic") return renderTraffic();
    if (state.tab === "connect") return renderConnect();
    if (state.tab === "keys") return renderKeys();
    return "";
  }

  function pageHeader(title, desc) {
    return "<div class=\"page-head\"><div><h1 class=\"page-title\">" + esc(title) + "</h1><p class=\"page-desc\">" + esc(desc) + "</p></div></div>";
  }

  function serverSelectHtml() {
    if (!state.servers.length) return "<div class=\"empty\">Create a server first.</div>";
    return "<div class=\"toolbar\"><label for=\"server-select\">Server</label><select id=\"server-select\" data-action=\"select-server\">" + state.servers.map(function (s) {
      return "<option value=\"" + s.id + "\"" + (String(s.id) === String(state.selectedServerId) ? " selected" : "") + ">" + esc(s.name) + " (" + esc(s.slug) + ")</option>";
    }).join("") + "</select></div>";
  }

  function renderCatalog() {
    return pageHeader("Catalog", "Browse every mock server and clone templates for bootcamp teams.") +
      "<section class=\"callout info\"><strong>Clone behavior:</strong> datasets, live rows, seed rows, and endpoints are copied. API keys and traffic are not copied. Clones are live immediately at <code>/mock/{slug}</code> and <code>/mcp/{slug}</code>.</section>" +
      "<section class=\"catalog-grid\">" + (state.catalog.length ? state.catalog.map(catalogCard).join("") : "<div class=\"empty\">No servers yet. Create one in the Servers tab.</div>") + "</section>";
  }

  function metricCount(value) {
    return Number.isFinite(Number(value)) ? Number(value) : 0;
  }

  function recipeCountForServer(serverId) {
    var seen = {};
    (state.recipes || []).forEach(function (recipe) {
      (recipe.tools || []).forEach(function (tool) {
        if (String(tool.server_id) === String(serverId)) seen[recipe.id] = true;
      });
    });
    return Object.keys(seen).length;
  }

  function authLabel(server) {
    return server && server.auth_mode === "api_key" ? "API key" : "No auth";
  }

  function catalogCard(server) {
    var recipeCount = recipeCountForServer(server.id);
    return "<article class=\"card catalog-card\"><div class=\"catalog-card-head\"><div><h3>" + esc(server.name) + "</h3><div class=\"catalog-slug\">" + esc(server.slug) + "</div></div>" +
      "<div class=\"auth-pill catalog-auth-pill\"><span class=\"auth-option\">" + esc(authLabel(server)) + "</span></div></div>" +
      "<div class=\"card-desc\">" + esc(server.description || "No description") + "</div>" +
      "<div class=\"catalog-metrics\"><span class=\"badge\">" + esc(metricCount(server.dataset_count)) + " datasets</span><span class=\"badge\">" + esc(metricCount(server.endpoint_count)) + " tools</span><span class=\"badge neutral\">" + esc(metricCount(server.row_count)) + " rows</span><span class=\"badge neutral\">" + esc(recipeCount) + " recipe" + (recipeCount === 1 ? "" : "s") + "</span></div>" +
      "<div class=\"actions\"><button class=\"btn small\" data-action=\"clone-server\" data-id=\"" + esc(server.id) + "\"" + disabledIfReadonly() + ">Clone</button><button class=\"btn small primary\" data-action=\"bulk-clone-server\" data-id=\"" + esc(server.id) + "\"" + disabledIfReadonly() + ">Bulk clone</button></div></article>";
  }

  function renderCohort() {
    return pageHeader("Cohort", "Bootcamp control panel for team servers, handouts, traffic, and reset-to-seed.") +
      "<div class=\"cohort-screen\">" +
      "<section class=\"panel cohort-no-print\"><div class=\"panel-heading\"><div><h2>Team servers</h2><p>Scan readiness before trainees start wiring MCP and REST endpoints into Copilot Studio.</p></div><button class=\"btn\" type=\"button\" data-action=\"refresh-cohort\">Refresh</button></div>" +
      cohortAuthCallout() + cohortSummaryHtml() + cohortToolbarHtml() + cohortTeamTable() + "</section>" +
      cohortHandoutHtml() + cohortTrafficSummaryHtml() + cohortResetHtml() + "</div>";
  }

  function cohortAuthCallout() {
    return "<div class=\"callout info\"><strong>Auth reality:</strong> API keys are global, hashed, and shown only once when created. There is no per-team key. No-auth servers are handout-ready; API-key servers need a key supplied out-of-band.</div>";
  }

  function cohortSummaryHtml() {
    var rows = state.cohort || [];
    var seeded = rows.filter(function (s) { return s.seeded; }).length;
    var inSync = rows.filter(function (s) { return s.in_sync; }).length;
    var active = rows.filter(function (s) { return metricCount(s.call_count) > 0; }).length;
    var calls = rows.reduce(function (total, s) { return total + metricCount(s.call_count); }, 0);
    return "<div class=\"catalog-metrics cohort-summary\"><span class=\"badge\">" + esc(rows.length) + " servers</span><span class=\"badge\">" + esc(seeded) + " seeded</span><span class=\"badge\">" + esc(inSync) + " in sync</span><span class=\"badge neutral\">" + esc(active) + " have calls</span><span class=\"badge neutral\">" + esc(calls) + " total calls</span></div>";
  }

  function cohortToolbarHtml() {
    return "<div class=\"toolbar cohort-toolbar\"><div class=\"form-row cohort-filter\"><label for=\"cohort-filter\">Filter teams</label><input id=\"cohort-filter\" value=\"" + esc(state.cohortFilter) + "\" placeholder=\"Filter by slug or name\"></div>" +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"button\" data-action=\"export-handout-markdown\">Copy as Markdown</button><button class=\"btn\" type=\"button\" data-action=\"download-handout-csv\">Download CSV</button><button class=\"btn\" type=\"button\" data-action=\"print-handout\">Print view</button></div><span class=\"help\" data-cohort-filter-summary></span></div>";
  }

  function cohortTeamTable() {
    if (state.cohortError) return "<div class=\"error-inline\">Cohort endpoint unavailable: " + esc(state.cohortError) + "</div>";
    if (!state.cohort.length) return "<div class=\"empty\">No team servers found. Bulk-clone from Catalog first.</div>";
    return "<div class=\"table-wrap\"><table class=\"cohort-table\"><thead><tr><th>Slug</th><th>Name</th><th>Status</th><th>Auth</th><th>Data</th><th>Calls</th><th>Last call</th><th>MCP URL</th><th>REST URL</th></tr></thead><tbody>" +
      state.cohort.map(cohortTeamRow).join("") + "</tbody></table></div><div id=\"cohort-no-results\" class=\"empty\" hidden>No team servers match that filter.</div>";
  }

  function cohortTeamRow(server) {
    var status = cohortStatus(server);
    return "<tr class=\"cohort-row\" data-search=\"" + esc(cohortSearchText(server)) + "\"><td><code>" + esc(server.slug) + "</code></td><td><strong>" + esc(server.name) + "</strong></td>" +
      "<td><span class=\"status-pill\" data-state=\"" + esc(status[0]) + "\">" + esc(status[1]) + "</span><div class=\"help\">" + esc(cohortStatusDetail(server)) + "</div></td>" +
      "<td>" + cohortAuthPill(server) + cohortKeyHelp(server) + "</td><td>" + esc(metricCount(server.dataset_count)) + " datasets · " + esc(metricCount(server.endpoint_count)) + " endpoints · " + esc(metricCount(server.row_count)) + " rows</td>" +
      "<td>" + esc(metricCount(server.call_count)) + "</td><td>" + esc(server.last_call_at || "never") + "</td><td>" + copyControl(cohortMcpUrl(server)) + "</td><td>" + copyControl(cohortRestUrl(server)) + "</td></tr>";
  }

  function cohortStatus(server) {
    if (!server.seeded) return ["bad", "No seed data"];
    if (!server.in_sync) return ["warn", "Data drift"];
    if (server.auth_mode === "api_key") return ["warn", "Needs key"];
    return ["ok", "Ready"];
  }

  function cohortStatusDetail(server) {
    if (!server.seeded) return "Reset will empty this server.";
    if (!server.in_sync) return "Live rows differ from seed rows.";
    if (server.auth_mode === "api_key") return "Seeded and synced, but not handout-ready without a key.";
    return "Seeded, in sync, no auth.";
  }

  function cohortAuthPill(server) {
    return "<span class=\"auth-pill cohort-auth-pill\"><span class=\"auth-option\">" + esc(authLabel(server)) + "</span></span>";
  }

  function cohortKeyHelp(server) {
    return server.auth_mode === "api_key" ? "<div class=\"help\">Global key required; no per-team key exists.</div>" : "";
  }

  function cohortMcpUrl(server) {
    return server.mcp_url || endpointUrl(server);
  }

  function cohortRestUrl(server) {
    return server.rest_url || restBaseUrl(server);
  }

  function copyControl(value) {
    return "<div class=\"copy-control\"><span class=\"url-text\">" + esc(value || "") + "</span><button class=\"btn small\" data-copy=\"" + esc(value || "") + "\">Copy</button></div>";
  }

  function cohortSearchText(server) {
    return String((server.slug || "") + " " + (server.name || "")).toLowerCase();
  }

  function cohortFilteredServers() {
    var query = (state.cohortFilter || "").trim().toLowerCase();
    return (state.cohort || []).filter(function (server) {
      return !query || cohortSearchText(server).indexOf(query) !== -1;
    });
  }

  function cohortHandoutHtml() {
    if (state.cohortError || !state.cohort.length) return "";
    return "<section class=\"panel cohort-print\"><div class=\"panel-heading cohort-no-print\"><div><h2>Handout preview</h2><p>Exports use the currently visible filtered rows.</p></div></div>" +
      "<h2 class=\"cohort-print-title\">Agent Integration Playground team handout</h2><p class=\"help cohort-print-note\">API-key servers require a global key supplied separately. Keys are never exported here.</p>" +
      "<div class=\"table-wrap\"><table class=\"cohort-handout-table\"><thead><tr><th>Team slug</th><th>MCP URL</th><th>REST URL</th><th>Auth note</th></tr></thead><tbody>" +
      state.cohort.map(cohortHandoutRow).join("") + "</tbody></table></div><div id=\"cohort-handout-empty\" class=\"empty\" hidden>No rows to export with the current filter.</div></section>";
  }

  function cohortHandoutRow(server) {
    return "<tr class=\"cohort-handout-row\" data-search=\"" + esc(cohortSearchText(server)) + "\"><td><code>" + esc(server.slug) + "</code></td><td>" + esc(cohortMcpUrl(server)) + "</td><td>" + esc(cohortRestUrl(server)) + "</td><td>" + esc(cohortAuthNote(server)) + "</td></tr>";
  }

  function cohortAuthNote(server) {
    return server.auth_mode === "api_key" ? "Requires global API key supplied out-of-band" : "Ready as-is";
  }

  function cohortTrafficSummaryHtml() {
    return "<section class=\"panel cohort-no-print\"><div class=\"panel-heading\"><div><h2>Traffic by team</h2><p>Find who has called their server and who is hitting errors.</p></div><button class=\"btn\" type=\"button\" data-action=\"refresh-cohort-traffic\">Refresh</button></div>" + cohortTrafficTable() + "</section>";
  }

  function cohortTrafficTable() {
    if (state.cohortTrafficError) return "<div class=\"error-inline\">Traffic summary unavailable: " + esc(state.cohortTrafficError) + "</div>";
    if (!state.cohortTrafficSummary.length) return "<div class=\"empty\">No traffic yet. Once trainees test their connector, calls show up here.</div>";
    return "<div class=\"table-wrap\"><table><thead><tr><th>Slug</th><th>Calls</th><th>OK</th><th>Errors</th><th>Avg duration</th><th>Last call</th></tr></thead><tbody>" +
      state.cohortTrafficSummary.map(function (r) {
        var errors = metricCount(r.error_count);
        return "<tr><td><code>" + esc(r.target_slug || "(unknown)") + "</code></td><td>" + esc(metricCount(r.call_count)) + "</td><td>" + esc(metricCount(r.ok_count)) + "</td><td>" + (errors ? "<span class=\"badge bad cohort-error-count\">" + esc(errors) + "</span>" : esc(errors)) + "</td><td>" + esc(metricCount(r.avg_duration_ms)) + " ms</td><td>" + esc(r.last_call_at || "never") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function cohortResetHtml() {
    return "<section class=\"panel cohort-no-print\"><div class=\"panel-heading\"><div><h2>Reset training environment</h2><p>Restore matching live rows from seed rows between cohorts.</p></div><span class=\"status-pill\" data-state=\"bad\">Destructive</span></div>" +
      "<form class=\"form-grid\" data-form=\"cohort-reset\"><div class=\"cohort-reset-grid\"><div class=\"form-row\"><label for=\"cohort-reset-prefix\">Prefix filter</label><input id=\"cohort-reset-prefix\" name=\"prefix\" placeholder=\"hr-team\"><span class=\"help\">Leave empty to reset ALL servers.</span></div><button class=\"btn danger\" type=\"submit\"" + disabledIfReadonly() + ">Reset matching servers</button></div></form></section>";
  }

  function renderServers() {
    var editing = state.servers.find(function (s) { return s.id === state.editingServerId; });
    return pageHeader("Servers", "Author mock MCP servers and copy their live URLs for Copilot Studio.") +
      "<div class=\"two-col\"><section class=\"panel\"><h2>" + (editing ? "Edit server" : "Create server") + "</h2><form class=\"form-grid\" data-form=\"server\">" +
      hidden("id", editing && editing.id) + input("slug", "Slug", editing && editing.slug, "acme-crm") + input("name", "Name", editing && editing.name, "Acme CRM") +
      area("description", "Description", editing && editing.description, "What this mock server represents") + select("auth_mode", "Authentication", editing && editing.auth_mode || "none", [["none", "No authentication"], ["api_key", "API key"]]) +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">" + (editing ? "Save server" : "Create server") + "</button>" + (editing ? "<button class=\"btn\" type=\"button\" data-action=\"cancel-server-edit\">Cancel</button>" : "") + "</div></form></section>" +
      "<section><div class=\"grid\">" + (state.servers.length ? state.servers.map(serverCard).join("") : "<div class=\"empty\">No servers yet.</div>") + "</div></section></div>";
  }

  function serverCard(s) {
    return "<article class=\"card\"><div><h3>" + esc(s.name) + "</h3><div class=\"card-desc\">" + esc(s.description || "No description") + "</div></div>" +
      "<div class=\"metric\">" + (s.endpoint_count || 0) + " tools · " + (s.dataset_count || 0) + " datasets · " + (s.row_count || 0) + " rows</div>" +
      "<div class=\"url-row\"><span class=\"url-text\">" + esc(endpointUrl(s)) + "</span><button class=\"btn small\" data-copy=\"" + esc(endpointUrl(s)) + "\">Copy</button></div>" +
      "<div class=\"actions\"><button class=\"btn small\" data-action=\"edit-server\" data-id=\"" + s.id + "\"" + disabledIfReadonly() + ">Edit</button><button class=\"btn small danger\" data-action=\"delete-server\" data-id=\"" + s.id + "\" data-name=\"" + esc(s.name) + "\"" + disabledIfReadonly() + ">Delete</button></div></article>";
  }

  function renderDatasets() {
    var dataset = selectedDataset();
    var detail = dataset && state.datasetDetails[dataset.id];
    var rows = dataset && state.datasetRows[dataset.id];
    return pageHeader("Datasets", "Paste JSON or CSV, inspect the inferred field schema, and manage seed data.") + serverSelectHtml() +
      (!selectedServer() ? "" : "<div class=\"two-col\"><section class=\"panel\"><h2>Create dataset</h2><form class=\"form-grid\" data-form=\"dataset\">" +
      input("key", "Dataset key", "", "customers") + input("id_field", "ID field", "id", "id") +
      "<div class=\"form-row\"><label for=\"data-format\">Input format</label><select id=\"data-format\" name=\"format\"><option value=\"auto\">Auto-detect</option><option value=\"json\">JSON</option><option value=\"csv\">CSV</option></select><span class=\"help\">Auto: first non-whitespace '[' or '{' means JSON; otherwise CSV.</span></div>" +
      area("rows_text", "Rows", "", "Paste a JSON array or CSV with a header row", "json") + "<div id=\"dataset-preview\" class=\"tabs-note\">Paste rows to preview the schema.</div>" +
      "<button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Create dataset</button></form><h3>Datasets</h3><div class=\"select-list\">" + datasetListHtml() + "</div></section>" +
      "<section class=\"panel\">" + datasetDetailHtml(detail, rows) + "</section></div>" + renderRelationships());
  }

  function emptyRecipeDraft() {
    return {
      id: null,
      slug: "",
      title: "",
      summary: "",
      department: "",
      skill: "beginner",
      agent_instructions: "",
      example_prompts: [],
      destinations: [],
      published: false,
      tools: []
    };
  }

  function recipeToDraft(recipe) {
    var draft = emptyRecipeDraft();
    Object.keys(draft).forEach(function (key) {
      if (recipe && recipe[key] != null) draft[key] = Array.isArray(recipe[key]) ? recipe[key].slice() : recipe[key];
    });
    draft.tools = (recipe && recipe.tools || []).slice().sort(function (a, b) { return (a.ordinal || 0) - (b.ordinal || 0); }).map(function (tool) {
      return { server_id: Number(tool.server_id), tool_name: tool.tool_name, server_slug: tool.server_slug, server_name: tool.server_name };
    });
    return draft;
  }

  function selectedRecipeDraft() {
    return state.recipeDraft || recipeToDraft(selectedRecipe()) || emptyRecipeDraft();
  }

  function recipeServerName(serverId) {
    var server = state.servers.find(function (s) { return String(s.id) === String(serverId); });
    return server ? server.name : "server #" + serverId;
  }

  function renderRecipes() {
    var draft = selectedRecipeDraft();
    return pageHeader("Recipes", "Author multi-server training scenarios with instructions, prompts, destinations, and MCP tools.") +
      "<div class=\"two-col\"><section class=\"panel\"><div class=\"panel-heading\"><div><h2>Recipes</h2><p>" + esc(state.recipes.length) + " scenario" + (state.recipes.length === 1 ? "" : "s") + "</p></div><button class=\"btn primary\" type=\"button\" data-action=\"new-recipe\"" + disabledIfReadonly() + ">New recipe</button></div><div class=\"select-list\">" + recipeListHtml() + "</div></section>" +
      "<section class=\"panel\"><h2>" + (draft.id ? "Edit recipe" : "Create recipe") + "</h2>" + recipeFormHtml(draft) + "</section></div>";
  }

  function recipeListHtml() {
    if (!state.recipes.length) return "<div class=\"empty\">No recipes yet.</div>";
    return state.recipes.map(function (recipe) {
      var skill = recipe.skill || "beginner";
      var badges = "<span class=\"badge skill-" + esc(skill) + "\">" + esc(skill) + "</span>" + (!recipe.published ? "<span class=\"badge neutral\">draft</span>" : "");
      return "<button type=\"button\" class=\"list-item " + (String(recipe.id) === String(state.selectedRecipeId) ? "active" : "") + "\" data-action=\"select-recipe\" data-id=\"" + esc(recipe.id) + "\"><span class=\"list-title\">" + esc(recipe.title) + "</span><span class=\"list-meta\">" + badges + " " + esc(recipe.department || "No department") + " · " + esc((recipe.tools || []).length) + " tools</span></button>";
    }).join("");
  }

  function recipeFormHtml(draft) {
    var deleteDisabled = draft.id ? disabledIfReadonly() : " disabled";
    return "<form class=\"form-grid\" data-form=\"recipe\">" + hidden("id", draft.id) +
      recipePublicCalloutHtml(draft) +
      input("slug", "Slug", draft.slug, "order-status-coach") +
      input("title", "Title", draft.title, "Order status coach") +
      input("department", "Department", draft.department, "Customer service") +
      select("skill", "Skill", draft.skill || "beginner", [["beginner", "beginner"], ["intermediate", "intermediate"], ["advanced", "advanced"]]) +
      area("summary", "Summary", draft.summary, "What trainees practice") +
      "<div class=\"form-row\"><label for=\"agent_instructions\">Agent instructions</label><textarea id=\"agent_instructions\" name=\"agent_instructions\" rows=\"9\" placeholder=\"Instructions trainees should paste into their agent\">" + esc(draft.agent_instructions || "") + "</textarea></div>" +
      "<div class=\"form-row\"><label><input type=\"checkbox\" name=\"published\" style=\"width:auto\"" + (draft.published ? " checked" : "") + "> Published</label></div>" +
      recipeRepeaterHtml("example_prompts", "Example prompts", draft.example_prompts, "Ask about an order", "add-recipe-prompt", "remove-recipe-prompt") +
      recipeRepeaterHtml("destinations", "Destinations", draft.destinations, "Copilot Studio", "add-recipe-destination", "remove-recipe-destination") +
      recipeToolsEditorHtml(draft) +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Save</button><button class=\"btn danger\" type=\"button\" data-action=\"delete-recipe\" data-id=\"" + esc(draft.id || "") + "\" data-name=\"" + esc(draft.title || draft.slug || "recipe") + "\"" + deleteDisabled + ">Delete</button><button class=\"btn\" type=\"button\" data-action=\"copy-recipe-link\">Copy public link</button><button class=\"btn\" type=\"button\" data-action=\"open-recipe-page\">Open public page</button><button class=\"btn\" type=\"button\" data-action=\"download-recipe-handout\"" + (draft.id ? "" : " disabled") + ">Download handout</button></div></form>";
  }

  function recipePublicCalloutHtml(draft) {
    return "<div id=\"recipe-public-callout\" class=\"callout " + (draft.published ? "ok" : "warn") + "\">" + (draft.published ? "Public URL: " + esc(recipeUrl(draft.slug)) : "This recipe is not publicly visible yet.") + "</div>";
  }

  function recipeRepeaterHtml(name, label, values, placeholder, addAction, removeAction) {
    var rows = (values || []).map(function (value, index) {
      return "<div class=\"actions\"><input name=\"" + esc(name) + "\" value=\"" + esc(value) + "\" placeholder=\"" + esc(placeholder) + "\"><button class=\"btn small danger\" type=\"button\" data-action=\"" + esc(removeAction) + "\" data-index=\"" + esc(index) + "\"" + disabledIfReadonly() + ">Remove</button></div>";
    }).join("");
    return "<div class=\"form-row\"><label>" + esc(label) + "</label><div class=\"form-grid\">" + (rows || "<div class=\"empty\">None yet.</div>") + "<div class=\"actions\"><button class=\"btn small\" type=\"button\" data-action=\"" + esc(addAction) + "\"" + disabledIfReadonly() + ">Add</button></div></div></div>";
  }

  function recipeToolsEditorHtml(draft) {
    var serverId = state.recipeToolServerId || (state.servers[0] && state.servers[0].id);
    var serverOptions = state.servers.map(function (server) {
      return "<option value=\"" + esc(server.id) + "\"" + (String(server.id) === String(serverId) ? " selected" : "") + ">" + esc(server.name) + " (" + esc(server.slug) + ")</option>";
    }).join("");
    return "<div class=\"form-row\"><label>Tools</label><div class=\"form-grid\">" +
      (!state.servers.length ? "<div class=\"empty\">Create a server first.</div>" : "<div class=\"actions\"><select id=\"recipe-tool-server\" data-action=\"select-recipe-tool-server\">" + serverOptions + "</select><select id=\"recipe-tool-name\" data-action=\"select-recipe-tool-name\">" + recipeToolOptionsHtml(serverId) + "</select><button class=\"btn small\" type=\"button\" data-action=\"add-recipe-tool\"" + disabledIfReadonly() + ">Add</button></div>") +
      recipeChosenToolsHtml(draft) + "</div></div>";
  }

  function recipeToolOptionsHtml(serverId) {
    var endpoints = state.recipeEndpointDetails[serverId] || [];
    if (!endpoints.length) return "<option value=\"\">No tools</option>";
    return endpoints.map(function (endpoint) {
      var selected = endpoint.tool_name === state.recipeToolName;
      return "<option value=\"" + esc(endpoint.tool_name) + "\"" + (selected ? " selected" : "") + ">" + esc(endpoint.tool_name) + "</option>";
    }).join("");
  }

  function recipeChosenToolsHtml(draft) {
    if (!draft.tools.length) return "<div class=\"empty\">No tools selected.</div>";
    return "<div class=\"select-list\">" + draft.tools.map(function (tool, index) {
      var serverLabel = tool.server_name || recipeServerName(tool.server_id);
      return "<div class=\"list-item\"><span class=\"list-title\">" + esc(tool.tool_name) + "</span><span class=\"list-meta\"><span class=\"badge neutral\">" + esc(serverLabel) + "</span></span><button class=\"btn small danger\" type=\"button\" data-action=\"remove-recipe-tool\" data-index=\"" + esc(index) + "\"" + disabledIfReadonly() + ">Remove</button></div>";
    }).join("") + "</div>";
  }

  function renderRelationships() {
    if (!selectedServer()) return "";
    var server = selectedServer();
    var rels = state.relationships || [];
    function relDatasetKey(id) {
      var ds = state.datasets.find(function (d) { return String(d.id) === String(id); });
      return ds ? ds.key : "dataset #" + id;
    }
    var tableHtml = rels.length
      ? "<div class=\"table-wrap\"><table><thead><tr><th>Name</th><th>Source</th><th>Target</th><th>Type</th><th>Expand</th><th>Inverse</th><th>Req</th><th></th></tr></thead><tbody>" +
        rels.map(function (r) {
          return "<tr><td><strong>" + esc(r.name) + "</strong>" + (r.description ? "<div class=\"help\">" + esc(r.description) + "</div>" : "") + "</td>" +
            "<td><code>" + esc(relDatasetKey(r.source_dataset_id) + "." + r.source_field) + "</code></td>" +
            "<td><code>" + esc(relDatasetKey(r.target_dataset_id) + "." + r.target_field) + "</code></td>" +
            "<td><span class=\"badge neutral\">" + esc(r.relation_type) + "</span></td>" +
            "<td><code>" + esc(r.expand_name) + "</code></td>" +
            "<td>" + (r.inverse_expand_name ? "<code>" + esc(r.inverse_expand_name) + "</code>" : "<span class=\"help\">—</span>") + "</td>" +
            "<td>" + (r.required ? "✓" : "") + "</td>" +
            "<td><button class=\"btn small danger\" data-action=\"delete-relationship\" data-id=\"" + r.id + "\" data-name=\"" + esc(r.name) + "\"" + disabledIfReadonly() + ">Delete</button></td></tr>";
        }).join("") + "</tbody></table></div>"
      : "<div class=\"empty\">No relationships for this server. Use the form below or &ldquo;Create demo relationships&rdquo; to get started.</div>";

    var validateHtml = "";
    var vr = state.validateResult;
    if (vr) {
      if (vr.ok && (!vr.issues || !vr.issues.length)) {
        validateHtml = "<div class=\"callout ok\"><strong>✓ Valid</strong> — " + vr.relationship_count + " relationship" + (vr.relationship_count === 1 ? "" : "s") + ", no issues found.</div>";
      } else {
        validateHtml = "<div class=\"callout warn\"><strong>" + (vr.issues ? vr.issues.length : 0) + " issue" + (vr.issues && vr.issues.length === 1 ? "" : "s") + " found</strong> (" + (vr.relationship_count || 0) + " relationships checked)</div>" +
          (vr.issues && vr.issues.length ? "<div class=\"table-wrap\"><table><thead><tr><th>Relationship</th><th>Severity</th><th>Message</th></tr></thead><tbody>" +
            vr.issues.map(function (issue) {
              return "<tr><td>" + esc(issue.name || "#" + issue.relationship_id) + "</td><td><span class=\"badge " + (issue.severity === "error" ? "bad" : "neutral") + "\">" + esc(issue.severity) + "</span></td><td>" + esc(issue.message) + "</td></tr>";
            }).join("") + "</tbody></table></div>" : "");
      }
    }

    var datasetOptions = state.datasets.map(function (d) { return [String(d.id), d.key]; });
    var createForm = "<form class=\"form-grid\" data-form=\"relationship\">" +
      input("name", "Relationship name", "", "order_lines_to_orders") +
      "<div class=\"form-row\"><label for=\"rel-src-dataset\">Source dataset</label><select id=\"rel-src-dataset\" name=\"source_dataset_id\" required>" +
      datasetOptions.map(function (o) { return "<option value=\"" + esc(o[0]) + "\">" + esc(o[1]) + "</option>"; }).join("") +
      "</select></div>" +
      input("source_field", "Source field", "", "order_id") +
      "<div class=\"form-row\"><label for=\"rel-tgt-dataset\">Target dataset</label><select id=\"rel-tgt-dataset\" name=\"target_dataset_id\" required>" +
      datasetOptions.map(function (o) { return "<option value=\"" + esc(o[0]) + "\">" + esc(o[1]) + "</option>"; }).join("") +
      "</select></div>" +
      input("target_field", "Target field", "", "id") +
      select("relation_type", "Relation type", "many_to_one", [["many_to_one", "many_to_one"], ["one_to_one", "one_to_one"]]) +
      input("expand_name", "Expand name", "", "order") +
      input("inverse_expand_name", "Inverse expand name (optional)", "", "lines") +
      "<div class=\"form-row\"><label class=\"rel-inline-label\"><input type=\"checkbox\" name=\"required\" style=\"width:auto\"> Required</label></div>" +
      area("description", "Description (optional)", "", "Human-readable description") +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Create relationship</button></div></form>";

    return "<section class=\"panel rel-panel\"><div class=\"page-head\"><div><h2>Relationships</h2><p class=\"page-desc\">" + rels.length + " relationship" + (rels.length === 1 ? "" : "s") + " for " + esc(server.name) + "</p></div>" +
      "<div class=\"actions\"><button class=\"btn\" data-action=\"validate-relationships\">Validate</button><button class=\"btn\" data-action=\"ensure-demo-relationships\" title=\"Idempotent — safe to run multiple times. Already-existing relationships are skipped.\"" + disabledIfReadonly() + ">Create demo relationships</button></div></div>" +
      "<div class=\"callout\">Relationships are metadata over JSON rows — they enable the <code>expand</code> query parameter to join datasets at query time but do not enforce foreign-key constraints in the database.</div>" +
      tableHtml + validateHtml +
      "<h3>Create relationship</h3>" + createForm + "</section>";
  }

  function datasetListHtml() {
    if (!state.datasets.length) return "<div class=\"empty\">No datasets for this server.</div>";
    return state.datasets.map(function (d) {
      return "<button type=\"button\" class=\"list-item " + (String(d.id) === String(state.selectedDatasetId) ? "active" : "") + "\" data-action=\"select-dataset\" data-id=\"" + d.id + "\"><span class=\"list-title\">" + esc(d.key) + "</span><span class=\"list-meta\">" + (d.row_count || 0) + " rows · " + (d.seed_count || 0) + " seed · id field " + esc(d.id_field) + "</span></button>";
    }).join("");
  }

  function datasetDetailHtml(detail, rows) {
    if (!selectedDataset()) return "<div class=\"empty\">Select a dataset.</div>";
    if (!detail || !rows) return "<div class=\"loading\">Loading dataset…</div>";
    return "<div class=\"page-head\"><div><h2>" + esc(detail.key) + "</h2><p class=\"page-desc\">" + (detail.row_count || 0) + " rows · " + (detail.seed_count || 0) + " seed rows · id field <code>" + esc(detail.id_field) + "</code></p></div><div class=\"actions\"><button class=\"btn\" data-action=\"edit-dataset\" data-id=\"" + detail.id + "\"" + disabledIfReadonly() + ">Edit metadata</button><button class=\"btn danger\" data-action=\"delete-dataset\" data-id=\"" + detail.id + "\" data-name=\"" + esc(detail.key) + "\"" + disabledIfReadonly() + ">Delete</button></div></div>" +
      datasetEditHtml(detail) + schemaHtml(detail.field_schema) +
      "<div class=\"actions\" style=\"margin:14px 0\"><button class=\"btn danger\" data-action=\"reset-seed\" data-id=\"" + detail.id + "\"" + disabledIfReadonly() + ">Reset to seed</button><button class=\"btn\" data-action=\"save-seed\" data-id=\"" + detail.id + "\"" + disabledIfReadonly() + ">Save as seed</button></div>" +
      "<form class=\"form-grid\" data-form=\"rows\"><input type=\"hidden\" name=\"id\" value=\"" + detail.id + "\"><div class=\"form-row\"><label>Rows JSON</label><textarea class=\"json\" name=\"rows\">" + esc(pretty(rows)) + "</textarea><span class=\"help\">Edit the JSON array, then save to replace all live rows.</span></div><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Save rows</button></form>";
  }

  function datasetEditHtml(detail) {
    if (state.editingDatasetId !== detail.id) return "";
    return "<form class=\"form-grid\" data-form=\"dataset-meta\"><input type=\"hidden\" name=\"id\" value=\"" + detail.id + "\">" +
      input("key", "Dataset key", detail.key, "customers") + input("id_field", "ID field", detail.id_field, "id") +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\">Save metadata</button><button class=\"btn\" type=\"button\" data-action=\"cancel-dataset-edit\">Cancel</button></div></form><hr>";
  }

  function schemaHtml(schema) {
    var keys = Object.keys(schema || {});
    if (!keys.length) return "<div class=\"tabs-note\">No field schema yet. Add rows to derive fields.</div>";
    return "<h3>Field schema</h3><div class=\"table-wrap\"><table><thead><tr><th>Field</th><th>Type</th></tr></thead><tbody>" + keys.map(function (k) {
      return "<tr><td><code>" + esc(k) + "</code></td><td>" + esc(schema[k]) + "</td></tr>";
    }).join("") + "</tbody></table></div>";
  }

  function renderEndpoints() {
    var editing = state.endpoints.find(function (e) { return e.id === state.editingEndpointId; });
    return pageHeader("Endpoints", "Define MCP tools from REST-like endpoint shapes. The inferred tool type is displayed live.") + serverSelectHtml() +
      (!selectedServer() ? "" : "<div class=\"two-col\"><section class=\"panel\"><h2>" + (editing ? "Edit endpoint" : "Create endpoint") + "</h2><form class=\"form-grid\" data-form=\"endpoint\">" + hidden("id", editing && editing.id) +
      input("path", "Path", editing && editing.path || "/orders", "/orders/{id}") + select("method", "Method", editing && editing.method || "GET", [["GET","GET"],["POST","POST"],["PUT","PUT"],["PATCH","PATCH"],["DELETE","DELETE"]]) +
      "<div class=\"tabs-note\"><strong>Inferred tool_type:</strong> <span id=\"tool-type-preview\"></span></div>" + input("tool_name", "Tool name", editing && editing.tool_name, "list_orders") + area("description", "Description", editing && editing.description, "What this tool does") + datasetSelect(editing && editing.dataset_id) +
      "<div class=\"form-row\" id=\"summary-fields-row\"><label>Summary fields</label><input name=\"summary_fields\" value=\"" + esc((editing && editing.summary_fields || []).join(", ")) + "\" placeholder=\"id, name, status\"><span class=\"help\">Only valid on list/search endpoints.</span></div>" +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">" + (editing ? "Save endpoint" : "Create endpoint") + "</button>" + (editing ? "<button class=\"btn\" type=\"button\" data-action=\"cancel-endpoint-edit\">Cancel</button>" : "") + "</div></form></section>" +
      "<section class=\"panel\"><h2>Tools</h2>" + restBaseCallout() + endpointsTable() + "</section></div>");
  }

  function restBaseCallout() {
    var server = selectedServer();
    if (!server) return "";
    return "<div class=\"tabs-note\"><strong>REST base URL</strong><div class=\"url-row\" style=\"margin-top:6px\"><span class=\"url-text\">" + esc(restBaseUrl(server)) + "</span><button class=\"btn small\" data-copy=\"" + esc(restBaseUrl(server)) + "\">Copy</button></div><span class=\"help\">Every endpoint below is callable over plain REST at this base and is described by the Swagger export.</span></div>";
  }

  function datasetSelect(selected) {
    return "<div class=\"form-row\"><label>Dataset</label><select name=\"dataset_id\" required>" + state.datasets.map(function (d) {
      return "<option value=\"" + d.id + "\"" + (String(d.id) === String(selected || (state.datasets[0] && state.datasets[0].id)) ? " selected" : "") + ">" + esc(d.key) + "</option>";
    }).join("") + "</select></div>";
  }

  function endpointsTable() {
    if (!state.endpoints.length) return "<div class=\"empty\">No endpoints for this server.</div>";
    return "<div class=\"table-wrap\"><table><thead><tr><th>Tool</th><th>Method</th><th>Path</th><th>Type</th><th>Dataset</th><th></th></tr></thead><tbody>" + state.endpoints.map(function (e) {
      var ds = state.datasets.find(function (d) { return d.id === e.dataset_id; });
      return "<tr><td><strong>" + esc(e.tool_name) + "</strong><div class=\"help\">" + esc(e.description || "") + "</div></td><td>" + esc(e.method) + "</td><td><code>" + esc(e.path) + "</code><div class=\"help\">" + esc(restUrl(selectedServer(), e)) + " <button class=\"btn small\" data-copy=\"" + esc(restUrl(selectedServer(), e)) + "\">Copy</button></div></td><td><span class=\"badge\">" + esc(e.tool_type || "?") + "</span></td><td>" + esc(ds ? ds.key : e.dataset_id) + "</td><td><div class=\"actions\"><button class=\"btn small\" data-action=\"edit-endpoint\" data-id=\"" + e.id + "\"" + disabledIfReadonly() + ">Edit</button><button class=\"btn small danger\" data-action=\"delete-endpoint\" data-id=\"" + e.id + "\" data-name=\"" + esc(e.tool_name) + "\"" + disabledIfReadonly() + ">Delete</button></div></td></tr>";
    }).join("") + "</tbody></table></div>";
  }

  function renderLlm() {
    var editing = state.llms.find(function (l) { return l.id === state.editingLlmId; });
    var details = editing ? state.llmDetails[editing.id] : null;
    var mode = editing ? editing.mode : "mock";
    return pageHeader("LLM endpoints", "Create OpenAI-compatible mock or proxy chat completion endpoints. Mock mode uses first-match-wins response rules by ordinal.") +
      "<div class=\"two-col\"><section class=\"panel\"><h2>" + (editing ? "Edit LLM endpoint" : "Create LLM endpoint") + "</h2><form class=\"form-grid\" data-form=\"llm\">" + hidden("id", editing && editing.id) +
      input("slug", "Slug", editing && editing.slug, "training-gpt") + input("name", "Name", editing && editing.name, "Training GPT") + area("description", "Description", editing && editing.description, "Mock or proxy endpoint") +
      select("mode", "Mode", mode, [["mock", "mock"], ["proxy", "proxy"]]) + input("model_name", "Model name", editing && editing.model_name || "playground-model", "playground-model") + select("auth_mode", "Authentication", editing && editing.auth_mode || "none", [["none", "No authentication"], ["api_key", "API key"]]) +
      "<div id=\"proxy-fields\">" + input("upstream_url", "Upstream URL", editing && editing.upstream_url, "https://...") + secret("upstream_key", "Upstream key", !!(editing && editing.upstream_key_set)) + input("upstream_deployment", "Upstream deployment", editing && editing.upstream_deployment, "") + area("system_prompt", "System prompt", editing && editing.system_prompt, "Optional system prompt") + "</div>" +
      "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">" + (editing ? "Save endpoint" : "Create endpoint") + "</button>" + (editing ? "<button class=\"btn\" type=\"button\" data-action=\"cancel-llm-edit\">Cancel</button>" : "") + "</div></form>" +
      (!editing || details ? "<div id=\"rules-section\"" + (mode === "mock" ? "" : " class=\"hidden\"") + ">" + rulesEditorHtml(!editing) + "</div>" : "") + "</section><section class=\"panel\"><h2>Endpoints</h2>" + llmListHtml() + "</section></div>";
  }

  function llmListHtml() {
    if (!state.llms.length) return "<div class=\"empty\">No LLM endpoints yet.</div>";
    return "<div class=\"select-list\">" + state.llms.map(function (l) {
      return "<article class=\"card\"><div><h3>" + esc(l.name) + " <span class=\"badge neutral\">" + esc(l.mode) + "</span></h3><div class=\"card-desc\">" + esc(l.description || "No description") + "</div></div><div class=\"url-row\"><span class=\"url-text\">" + esc(llmUrl(l)) + "</span><button class=\"btn small\" data-copy=\"" + esc(llmUrl(l)) + "\">Copy</button></div><div class=\"actions\"><button class=\"btn small\" data-action=\"edit-llm\" data-id=\"" + l.id + "\"" + disabledIfReadonly() + ">Edit</button><button class=\"btn small danger\" data-action=\"delete-llm\" data-id=\"" + l.id + "\" data-name=\"" + esc(l.name) + "\"" + disabledIfReadonly() + ">Delete</button></div></article>";
    }).join("") + "</div>";
  }

  function rulesEditorHtml(isNew) {
    // A brand new endpoint has no id yet, so there is nothing to attach rules to.
    // submitLlm() saves whatever is in this editor right after it creates the
    // endpoint, which is why the rules survive — but "Save rules" on its own cannot.
    var note = isNew
      ? "<p class=\"help\">Rules you add here are saved together with the endpoint. \"Save rules\" becomes available once the endpoint exists.</p>"
      : "";
    return "<hr><h3>Mock response rules</h3><p class=\"help\">First match wins by ordinal. Reorder rules to change priority.</p>" + note + "<div class=\"form-grid\" id=\"rules-editor\">" +
      (state.llmRules.length ? state.llmRules.map(ruleHtml).join("") : "<div class=\"empty\">No rules yet. Add one; mock endpoints need a response.</div>") +
      "<div class=\"actions\"><button class=\"btn\" type=\"button\" data-action=\"add-rule\"" + disabledIfReadonly() + ">Add rule</button><button class=\"btn primary\" type=\"button\" data-action=\"save-rules\"" + (isNew ? " disabled" : disabledIfReadonly()) + ">Save rules</button></div></div>";
  }

  function ruleHtml(rule, i) {
    return "<div class=\"rule-card\" data-rule-index=\"" + i + "\"><div class=\"rule-header\"><strong>Ordinal " + (i + 1) + "</strong><div class=\"actions\"><button class=\"btn small\" data-action=\"rule-up\" data-index=\"" + i + "\"" + disabledIfReadonly() + ">↑</button><button class=\"btn small\" data-action=\"rule-down\" data-index=\"" + i + "\"" + disabledIfReadonly() + ">↓</button><button class=\"btn small danger\" data-action=\"remove-rule\" data-index=\"" + i + "\"" + disabledIfReadonly() + ">Remove</button></div></div>" +
      select("rule_match_type_" + i, "Match type", rule.match_type, [["always","always"],["contains","contains"],["regex","regex"]]) + input("rule_match_value_" + i, "Match value", rule.match_value, "leave empty for always") + area("rule_response_" + i, "Response", rule.response, "Mock response text") + "</div>";
  }

  function toolTypeForEndpoint(endpoint) {
    return inferToolType(endpoint.method, endpoint.path) || endpoint.tool_type || "tool";
  }

  function toolTypeLabel(type) {
    return String(type || "tool").toUpperCase();
  }

  function selectedTestEndpoint() {
    var selected = state.testTool || (state.endpoints[0] ? state.endpoints[0].tool_name : "");
    return state.endpoints.find(function (e) { return e.tool_name === selected; }) || state.endpoints[0] || null;
  }

  function toolRailHtml(selected) {
    if (!state.endpoints.length) return "<div class=\"empty\">No tools for this server yet.</div>";
    var order = ["list", "get", "search", "create", "update", "delete", "tool"];
    var groups = {};
    state.endpoints.forEach(function (endpoint) {
      var type = toolTypeForEndpoint(endpoint);
      if (!groups[type]) groups[type] = [];
      groups[type].push(endpoint);
    });
    var html = order.concat(Object.keys(groups).filter(function (type) { return order.indexOf(type) === -1; })).filter(function (type, index, arr) {
      return arr.indexOf(type) === index && groups[type] && groups[type].length;
    }).map(function (type) {
      return "<div class=\"tool-group\" data-tool-group=\"" + esc(type) + "\"><div class=\"tool-group-title\">" + esc(toolTypeLabel(type)) + "</div>" +
        groups[type].map(function (endpoint) {
          var search = [endpoint.tool_name, endpoint.method, endpoint.path, type].join(" ").toLowerCase();
          return "<button type=\"button\" class=\"tool-row " + (endpoint.tool_name === selected ? "active" : "") + "\" data-action=\"select-test-tool\" data-tool=\"" + esc(endpoint.tool_name) + "\" data-search=\"" + esc(search) + "\"><span class=\"tool-type-badge\" data-type=\"" + esc(type) + "\">" + esc(type) + "</span><span class=\"kv\"><span class=\"tool-name\">" + esc(endpoint.tool_name) + "</span><span class=\"tool-meta\">" + esc(endpoint.method + " " + endpoint.path) + "</span></span></button>";
        }).join("") + "</div>";
    }).join("");
    return "<input id=\"tool-search\" class=\"tool-search\" value=\"" + esc(state.testSearch) + "\" placeholder=\"Search " + esc(state.endpoints.length) + " tools…\">" +
      "<div class=\"tool-groups\">" + html + "</div><div id=\"tool-no-results\" class=\"empty\" hidden>No tools match that search.</div>";
  }

  function responseStatusState() {
    if (state.testStatus == null) return "idle";
    return state.testStatus >= 400 ? "bad" : "ok";
  }

  function responseHtml() {
    if (state.testResult === null && state.testStatus === null) return "<div class=\"response-placeholder\">No request sent yet.</div>";
    return "<p class=\"help\"><span class=\"status-pill\" data-state=\"" + esc(responseStatusState()) + "\">HTTP " + esc(state.testStatus) + "</span> · Elapsed: " + esc(state.testElapsed) + " ms</p>" +
      (state.testCurl ? "<div class=\"url-row\"><span class=\"url-text\">" + esc(state.testCurl) + "</span><button class=\"btn small\" data-copy=\"" + esc(state.testCurl) + "\">Copy</button></div>" : "") +
      "<pre class=\"prebox\">" + esc(pretty(state.testResult)) + "</pre>";
  }

  function renderTestConsole() {
    var server = selectedServer();
    var header = pageHeader("Test console", "Call an endpoint over plain REST the way a custom connector does, or through the internal executor path.") + serverSelectHtml();
    if (!server) return header;
    var endpoint = selectedTestEndpoint();
    var selected = endpoint ? endpoint.tool_name : "";
    var modes = [["rest", "REST — /mock/" + server.slug], ["executor", "Executor — /api/servers/…/tools/…/call"]].map(function (m) {
      return "<option value=\"" + esc(m[0]) + "\"" + (state.testMode === m[0] ? " selected" : "") + ">" + esc(m[1]) + "</option>";
    }).join("");
    var keyRow = state.testMode === "rest" && server.auth_mode === "api_key"
      ? "<div class=\"form-row\"><label>API key</label><input name=\"api_key\" value=\"" + esc(state.testApiKey) + "\" placeholder=\"mcpp_…\"><span class=\"help\">This server requires a key. Sent as <code>X-API-Key</code>; the admin session cookie is not used.</span></div>"
      : "";
    return header + "<section class=\"panel test-panel\"><div class=\"panel-heading\"><div><h2>Try it before you wire it up</h2><p>This console calls the real endpoint. The responses are live, not canned.</p></div><span class=\"status-pill\" data-state=\"connected\">" + esc(state.endpoints.length) + " tools · connected</span></div>" +
      "<form data-form=\"tool-call\"><input type=\"hidden\" name=\"tool_name\" id=\"tool-select\" value=\"" + esc(selected) + "\"><div class=\"tool-console-grid\"><aside class=\"tool-rail\">" + toolRailHtml(selected) + "</aside>" +
      "<section class=\"request-pane\"><div class=\"request-lead\"><div><h3>Pick a tool to build a request.</h3><p id=\"tool-help\" class=\"help\"></p></div><button class=\"btn primary\" type=\"submit\"" + (selected ? "" : " disabled") + ">Run tool ▸</button></div>" +
      "<div class=\"form-grid\"><div class=\"form-row\"><label>Surface</label><select name=\"mode\" id=\"test-mode\">" + modes + "</select><span class=\"help\">Both surfaces hit real data. REST is what Power Platform calls; Executor is admin-gated but, as admin, also runs write tools. A create or delete tool will change your seed data.</span></div>" +
      keyRow + "<div class=\"form-row\"><label>Parameters JSON</label><textarea class=\"json\" name=\"params\">" + esc(state.testParams) + "</textarea></div></div>" +
      "<div class=\"section-divider\"></div><div class=\"response-block\"><div class=\"response-head\">Response <span class=\"status-pill\" data-state=\"" + esc(responseStatusState()) + "\">" + esc(state.testStatus == null ? "idle" : "HTTP " + state.testStatus) + "</span></div>" + responseHtml() + "</div></section></div></form></section>";
  }

  function buildRestRequest(server, endpoint, params) {
    var rest = Object.assign({}, params);
    var path = endpoint.path.replace(/\{([^}]+)\}/g, function (_match, name) {
      var value = rest[name];
      delete rest[name];
      return encodeURIComponent(value == null ? "" : String(value));
    });
    var method = String(endpoint.method || "GET").toUpperCase();
    var sendsBody = method === "POST" || method === "PUT" || method === "PATCH";
    var query = "";
    if (!sendsBody) {
      var pairs = Object.keys(rest).filter(function (k) { return rest[k] != null; }).map(function (k) {
        return encodeURIComponent(k) + "=" + encodeURIComponent(String(rest[k]));
      });
      if (pairs.length) query = "?" + pairs.join("&");
    }
    return { method: method, url: restBaseUrl(server) + path + query, body: sendsBody ? JSON.stringify(rest) : null };
  }

  function curlFor(request, apiKey) {
    var out = "curl -X " + request.method + " '" + request.url + "'";
    if (apiKey) out += " -H 'X-API-Key: " + apiKey + "'";
    if (request.body != null) out += " -H 'Content-Type: application/json' -d '" + request.body + "'";
    return out;
  }

  function captureTestForm() {
    var form = document.querySelector("form[data-form='tool-call']");
    if (!form) return;
    var data = formData(form);
    if (data.tool_name) state.testTool = data.tool_name;
    if (data.params != null) state.testParams = data.params;
    if (data.api_key != null) state.testApiKey = data.api_key;
  }

  function renderTraffic() {
    return pageHeader("Traffic", "Inspect requests and responses recorded by the playground.") +
      "<div class=\"toolbar\"><button class=\"btn\" data-action=\"refresh-traffic\">Refresh</button><button class=\"btn danger\" data-action=\"clear-traffic\"" + disabledIfReadonly() + ">Clear</button><label><input type=\"checkbox\" data-action=\"auto-traffic\"" + (state.autoTraffic ? " checked" : "") + "> Auto-refresh every 3s</label></div>" + trafficTable();
  }

  function trafficTable() {
    if (!state.traffic.length) return "<div class=\"empty\">No traffic recorded.</div>";
    return "<div class=\"table-wrap\"><table><thead><tr><th>Kind</th><th>Target</th><th>Tool</th><th>Status</th><th>Duration</th><th>Actor</th><th>Created</th></tr></thead><tbody>" + state.traffic.map(function (r) {
      var open = String(state.expandedTraffic) === String(r.id);
      var main = "<tr class=\"traffic-row\" tabindex=\"0\" data-action=\"toggle-traffic\" data-id=\"" + r.id + "\"><td>" + esc(r.kind) + "</td><td>" + esc(r.target_slug || "") + "</td><td>" + esc(r.tool_name || "") + "</td><td>" + esc(r.status) + "</td><td>" + esc(r.duration_ms) + " ms</td><td>" + esc(r.actor || "") + "</td><td>" + esc(r.created_at) + "</td></tr>";
      var detail = open ? "<tr><td colspan=\"7\"><div class=\"inline-grid\"><div><strong>Request</strong><pre class=\"prebox\">" + esc(pretty(r.request)) + "</pre></div><div><strong>Response</strong><pre class=\"prebox\">" + esc(pretty(r.response)) + "</pre></div></div></td></tr>" : "";
      return main + detail;
    }).join("") + "</tbody></table></div>";
  }

  function renderConnect() {
    var server = selectedServer();
    return pageHeader("Connect it", "Copy the exact fields into Microsoft setup screens.") + serverSelectHtml() +
      (!server ? "" : "<section class=\"panel connect-panel\"><div class=\"panel-heading\"><div><h2>Connect it to your assistant</h2><p>Pick your platform. The fields below change to match its own setup screen.</p></div></div>" +
      platformTabsHtml() + connectStepsHtml() + authGroupHtml(server) + connectFieldsHtml(server) +
      "<div class=\"callout warn\"><strong>Generative orchestration must be ON in the agent's settings — the MCP tools will not be called otherwise.</strong></div>" +
      "<div class=\"callout info\"><strong>💡 DLP:</strong> the tenant's Data Loss Prevention policy may block a custom/unclassified connector. A maker may need an admin to allow it.</div></section>");
  }

  function platformTabsHtml() {
    var platforms = [
      ["copilot", "◇", "Copilot Studio"],
      ["automate", "⚡", "Power Automate"],
      ["apps", "▣", "Power Apps"],
      ["vscode", "⌘", "VS Code"],
      ["http", "{}", "Raw HTTP"]
    ];
    if (!state.connectPlatform || !platforms.some(function (p) { return p[0] === state.connectPlatform; })) state.connectPlatform = "copilot";
    return "<div class=\"platform-tabs\">" + platforms.map(function (platform) {
      return "<button type=\"button\" class=\"platform-tab " + (state.connectPlatform === platform[0] ? "active" : "") + "\" data-action=\"select-connect-platform\" data-platform=\"" + esc(platform[0]) + "\"><span class=\"platform-icon\">" + esc(platform[1]) + "</span>" + esc(platform[2]) + "</button>";
    }).join("") + "</div>";
  }

  function connectStepsHtml() {
    var steps = {
      copilot: [["Open tools", "In Copilot Studio, open the agent and go to Tools."], ["Add MCP server", "Choose Add tool, then MCP server, and paste the server fields."], ["Enable orchestration", "Turn generative orchestration on and add an API key if required."]],
      automate: [["Export OpenAPI", "Download the OpenAPI file below while signed in — the export URL is admin-only."], ["Create connector", "In Power Automate, create a custom connector from the OpenAPI file."], ["Use in flows", "Create a connection and call the REST operations from a flow."]],
      apps: [["Export OpenAPI", "Download the OpenAPI file below while signed in — the export URL is admin-only."], ["Create connector", "In Power Apps, create a custom connector from the OpenAPI file."], ["Add to app", "Create a connection, then use the connector actions in formulas."]],
      vscode: [["Copy server URL", "Use the MCP server URL in your MCP-capable VS Code client."], ["Set auth header", "If API key auth is enabled, send X-API-Key with a key from this app."], ["Test calls", "Run a tool call against the selected server before training."]],
      http: [["Copy REST base", "Use the REST base URL for direct endpoint calls."], ["Send request", "Send JSON bodies for write methods and query strings for reads."], ["Add auth header", "If required, include X-API-Key with each request."]]
    };
    var selected = steps[state.connectPlatform] || steps.copilot;
    return "<div class=\"step-list\">" + selected.map(function (step, index) {
      return "<div class=\"step-card\"><div class=\"step-number\">" + esc(index + 1) + "</div><div><div class=\"step-title\">" + esc(step[0]) + "</div><div class=\"help\">" + esc(step[1]) + "</div></div></div>";
    }).join("") + "</div>";
  }

  function connectFieldsHtml(server) {
    var swaggerUrl = window.location.origin + "/api/servers/" + server.id + "/swagger";
    var auth = server.auth_mode === "api_key" ? "API key" : "No authentication";
    var sets = {
      copilot: [["Server name", server.name, true], ["Server description", server.description || "Synthetic MCP server from Agent Integration Playground", true], ["Server URL", endpointUrl(server), true], ["Authentication", auth, false], ["REST base URL", restBaseUrl(server), false], ["Swagger export URL (admin-only)", swaggerUrl, false]],
      automate: [["REST base URL", restBaseUrl(server), true], ["Swagger export URL (admin-only)", swaggerUrl, true], ["Authentication", auth, false], ["Server name", server.name, false], ["Server description", server.description || "Synthetic MCP server from Agent Integration Playground", false], ["Server URL", endpointUrl(server), false]],
      apps: [["REST base URL", restBaseUrl(server), true], ["Swagger export URL (admin-only)", swaggerUrl, true], ["Authentication", auth, false], ["Server name", server.name, false], ["Server description", server.description || "Synthetic MCP server from Agent Integration Playground", false], ["Server URL", endpointUrl(server), false]],
      vscode: [["Server name", server.name, true], ["Server URL", endpointUrl(server), true], ["Authentication", auth, false], ["Server description", server.description || "Synthetic MCP server from Agent Integration Playground", false], ["REST base URL", restBaseUrl(server), false], ["Swagger export URL (admin-only)", swaggerUrl, false]],
      http: [["REST base URL", restBaseUrl(server), true], ["Authentication", auth, false], ["Swagger export URL (admin-only)", swaggerUrl, false], ["Server URL", endpointUrl(server), false], ["Server name", server.name, false], ["Server description", server.description || "Synthetic MCP server from Agent Integration Playground", false]]
    };
    var fields = sets[state.connectPlatform] || sets.copilot;
    return "<div class=\"step-copy-grid\">" + fields.map(function (field) { return copyLine(field[0], field[1], field[2]); }).join("") + "</div>" +
      "<div class=\"callout\"><strong>The Swagger export URL is admin-only.</strong>" +
      "<p class=\"help\">Attendees are not signed in here, so that URL returns 401 for them. Download the file while you are signed in and share it — Power Automate and Power Apps both import a custom connector from an uploaded OpenAPI file.</p>" +
      "<button class=\"btn small\" type=\"button\" data-action=\"download-swagger\" data-id=\"" + esc(server.id) + "\" data-slug=\"" + esc(server.slug) + "\">Download OpenAPI file</button></div>";
  }

  function authGroupHtml(server) {
    var hasKey = server.auth_mode === "api_key";
    return "<div class=\"form-row\"><label>Authentication</label><div class=\"auth-pill\"><label class=\"auth-option\"><input type=\"radio\" disabled" + (!hasKey ? " checked" : "") + "> None</label><label class=\"auth-option\"><input type=\"radio\" disabled" + (hasKey ? " checked" : "") + "> API key</label><label class=\"auth-option\"><input type=\"radio\" disabled> OAuth 2.0</label></div><span class=\"help\">Agent Integration Playground currently emits no-auth or API-key setup values. API keys are sent as <code>X-API-Key</code>.</span></div>";
  }

  function copyLine(label, value, required) {
    return "<div class=\"copy-line\"><strong>" + esc(label) + (required ? "<span class=\"field-required\">*</span>" : "") + "</strong><div class=\"copy-control\"><span class=\"url-text\">" + esc(value) + "</span><button class=\"btn small\" data-copy=\"" + esc(value) + "\">Copy</button></div></div>";
  }

  function renderKeys() {
    return pageHeader("API keys", "Create readonly or admin keys. Plaintext keys are shown once.") +
      (state.newKey ? "<section class=\"callout warn\"><strong>Copy this key now. It will not be shown again.</strong><div class=\"url-row\" style=\"margin-top:8px\"><span class=\"url-text\">" + esc(state.newKey.key) + "</span><button class=\"btn small\" data-copy=\"" + esc(state.newKey.key) + "\">Copy</button><button class=\"btn small\" data-action=\"dismiss-key\">Dismiss</button></div></section>" : "") +
      "<div class=\"two-col\"><section class=\"panel\"><h2>Create key</h2><form class=\"form-grid\" data-form=\"key\">" + input("label", "Label", "", "Trainer laptop") + select("scope", "Scope", "readonly", [["readonly","readonly"],["admin","admin"]]) + "<button class=\"btn primary\" type=\"submit\"" + disabledIfReadonly() + ">Create key</button></form></section><section class=\"panel\"><h2>Keys</h2>" + keysTable() + "</section></div>";
  }

  function keysTable() {
    if (!state.keys.length) return "<div class=\"empty\">No active keys.</div>";
    return "<div class=\"table-wrap\"><table><thead><tr><th>Label</th><th>Scope</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>" + state.keys.map(function (k) {
      return "<tr><td>" + esc(k.label) + "</td><td><span class=\"badge neutral\">" + esc(k.scope) + "</span></td><td>" + esc(k.created_at) + "</td><td>" + esc(k.last_used_at || "never") + "</td><td><button class=\"btn small danger\" data-action=\"delete-key\" data-id=\"" + k.id + "\" data-name=\"" + esc(k.label) + "\"" + disabledIfReadonly() + ">Revoke</button></td></tr>";
    }).join("") + "</tbody></table></div>";
  }

  function input(name, label, value, placeholder) {
    return "<div class=\"form-row\"><label for=\"" + esc(name) + "\">" + esc(label) + "</label><input id=\"" + esc(name) + "\" name=\"" + esc(name) + "\" value=\"" + esc(value || "") + "\" placeholder=\"" + esc(placeholder || "") + "\"></div>";
  }

  function area(name, label, value, placeholder, cls) {
    return "<div class=\"form-row\"><label for=\"" + esc(name) + "\">" + esc(label) + "</label><textarea id=\"" + esc(name) + "\" name=\"" + esc(name) + "\" class=\"" + esc(cls || "") + "\" placeholder=\"" + esc(placeholder || "") + "\">" + esc(value || "") + "</textarea></div>";
  }

  function select(name, label, value, options) {
    return "<div class=\"form-row\"><label for=\"" + esc(name) + "\">" + esc(label) + "</label><select id=\"" + esc(name) + "\" name=\"" + esc(name) + "\">" + options.map(function (o) { return "<option value=\"" + esc(o[0]) + "\"" + (String(o[0]) === String(value) ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("") + "</select></div>";
  }

  function hidden(name, value) {
    return value ? "<input type=\"hidden\" name=\"" + esc(name) + "\" value=\"" + esc(value) + "\">" : "";
  }

  // The stored upstream credential is never sent to the browser, so this field is
  // always blank. Leaving it blank on save keeps the existing key; the checkbox is
  // the only way to remove one.
  function secret(name, label, isSet) {
    var help = isSet
      ? "A key is stored. Leave blank to keep it, or type a new one to replace it."
      : "No key stored yet.";
    var clear = isSet
      ? "<label class=\"inline-check\"><input type=\"checkbox\" name=\"clear_upstream_key\" value=\"1\"> Clear stored key</label>"
      : "";
    return "<div class=\"form-row\"><label for=\"" + esc(name) + "\">" + esc(label) + "</label>" +
      "<input id=\"" + esc(name) + "\" name=\"" + esc(name) + "\" type=\"password\" autocomplete=\"new-password\" value=\"\" placeholder=\"" + (isSet ? "••••••••  (unchanged)" : "Paste the upstream key") + "\">" +
      "<p class=\"help\">" + esc(help) + "</p>" + clear + "</div>";
  }

  function afterRender() {
    updateToolTypePreview();
    updateProxyFields();
    updateToolHelp();
    applyToolSearch();
    applyCohortFilter();
    updateDatasetPreview();
    updateRecipePublicCallout();
  }

  function formData(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  function readRecipeListInputs(form, name) {
    return Array.prototype.map.call(form.querySelectorAll("[name='" + name + "']"), function (inputEl) {
      return inputEl.value.trim();
    }).filter(Boolean);
  }

  function captureRecipeDraft() {
    var form = document.querySelector("form[data-form='recipe']");
    if (!form) return selectedRecipeDraft();
    var data = formData(form);
    state.recipeDraft = {
      id: data.id ? Number(data.id) : null,
      slug: (data.slug || "").trim(),
      title: (data.title || "").trim(),
      summary: data.summary || "",
      department: (data.department || "").trim(),
      skill: data.skill || "beginner",
      agent_instructions: data.agent_instructions || "",
      example_prompts: readRecipeListInputs(form, "example_prompts"),
      destinations: readRecipeListInputs(form, "destinations"),
      published: !!form.elements.published.checked,
      tools: (state.recipeDraft && state.recipeDraft.tools || []).slice()
    };
    return state.recipeDraft;
  }

  function recipePayloadFromForm(form) {
    var draft = captureRecipeDraft();
    return {
      slug: draft.slug,
      title: draft.title,
      summary: draft.summary,
      department: draft.department,
      skill: draft.skill,
      agent_instructions: draft.agent_instructions,
      example_prompts: draft.example_prompts,
      destinations: draft.destinations,
      published: draft.published,
      tools: draft.tools.map(function (tool) {
        return { server_id: Number(tool.server_id), tool_name: tool.tool_name };
      })
    };
  }

  function updateRecipePublicCallout() {
    var callout = document.getElementById("recipe-public-callout");
    var form = document.querySelector("form[data-form='recipe']");
    if (!callout || !form) return;
    var slug = (form.elements.slug.value || "").trim();
    var published = !!form.elements.published.checked;
    callout.className = "callout " + (published ? "ok" : "warn");
    callout.textContent = published ? "Public URL: " + recipeUrl(slug) : "This recipe is not publicly visible yet.";
  }

  function parseCsv(text) {
    var rows = [];
    var row = [];
    var cell = "";
    var inQuotes = false;
    for (var i = 0; i < text.length; i += 1) {
      var ch = text[i];
      var next = text[i + 1];
      if (ch === '"') {
        if (inQuotes && next === '"') { cell += '"'; i += 1; }
        else inQuotes = !inQuotes;
      } else if (ch === "," && !inQuotes) {
        row.push(cell); cell = "";
      } else if ((ch === "\n" || ch === "\r") && !inQuotes) {
        if (ch === "\r" && next === "\n") i += 1;
        row.push(cell); rows.push(row); row = []; cell = "";
      } else {
        cell += ch;
      }
    }
    row.push(cell); rows.push(row);
    rows = rows.filter(function (r) { return r.some(function (c) { return c.trim() !== ""; }); });
    if (!rows.length) return [];
    var headers = rows[0].map(function (h) { return h.trim(); });
    return rows.slice(1).map(function (r) {
      var obj = {};
      headers.forEach(function (h, idx) { if (h) obj[h] = coerceCsv(r[idx] == null ? "" : r[idx]); });
      return obj;
    });
  }

  function coerceCsv(value) {
    var trimmed = value.trim();
    if (trimmed === "") return "";
    if (trimmed === "true") return true;
    if (trimmed === "false") return false;
    if (trimmed === "null") return null;
    if (/^-?\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
    if (/^-?\d+\.\d+$/.test(trimmed)) return Number.parseFloat(trimmed);
    return value;
  }

  function parseRows(text, format) {
    var source = (text || "").trim();
    if (!source) return [];
    var mode = format === "auto" ? (/^[\[{]/.test(source) ? "json" : "csv") : format;
    if (mode === "json") {
      var parsed = JSON.parse(source);
      if (!Array.isArray(parsed) || parsed.some(function (r) { return r == null || typeof r !== "object" || Array.isArray(r); })) throw new Error("JSON rows must be an array of objects.");
      return parsed;
    }
    return parseCsv(source);
  }

  function deriveSchema(rows) {
    var schema = {};
    rows.forEach(function (row) {
      Object.keys(row).forEach(function (k) {
        if (schema[k]) return;
        var v = row[k];
        schema[k] = typeof v === "boolean" ? "boolean" : Number.isInteger(v) ? "integer" : typeof v === "number" ? "number" : "string";
      });
    });
    return schema;
  }

  function inferToolType(method, path) {
    var p = (path || "").trim();
    if (!p.startsWith("/")) return null;
    var segments = p.replace(/\/+$/, "").split("/").slice(1);
    if (!segments.length || segments.some(function (s) { return !s; }) || /^\{.+\}$/.test(segments[0])) return null;
    method = (method || "GET").toUpperCase();
    if (segments.length === 1) {
      if (method === "GET") return "list";
      if (method === "POST") return "create";
    }
    if (segments.length === 2) {
      if (method === "GET" && segments[1] === "search") return "search";
      if (/^\{.+\}$/.test(segments[1])) {
        if (method === "GET") return "get";
        if (method === "PUT" || method === "PATCH") return "update";
        if (method === "DELETE") return "delete";
      }
    }
    return null;
  }

  function updateToolTypePreview() {
    var preview = document.getElementById("tool-type-preview");
    if (!preview) return;
    var form = preview.closest("form");
    var type = inferToolType(form.elements.method.value, form.elements.path.value);
    preview.innerHTML = type ? "<span class=\"badge\">" + esc(type) + "</span>" : "<span class=\"badge bad\">No supported shape</span>";
    var summary = document.getElementById("summary-fields-row");
    if (summary) summary.classList.toggle("hidden", !(type === "list" || type === "search"));
  }

  function updateProxyFields() {
    var mode = document.querySelector("form[data-form='llm'] select[name='mode']");
    var fields = document.getElementById("proxy-fields");
    if (!mode) return;
    if (fields) fields.classList.toggle("hidden", mode.value !== "proxy");
    // The rules editor only applies to mock mode. It is always in the DOM so the
    // switch works without a re-render, which would discard whatever was typed.
    var rules = document.getElementById("rules-section");
    if (rules) rules.classList.toggle("hidden", mode.value !== "mock");
  }

  function updateToolHelp() {
    var selectEl = document.getElementById("tool-select");
    var help = document.getElementById("tool-help");
    if (!selectEl || !help) return;
    var endpoint = state.endpoints.find(function (e) { return e.tool_name === selectEl.value; });
    if (!endpoint) { help.textContent = "No tools for this server."; return; }
    var param = (endpoint.path.match(/\{([^}]+)\}/) || [])[1];
    var type = toolTypeForEndpoint(endpoint);
    var hints = "<strong>" + esc(endpoint.method + " " + endpoint.path) + "</strong> · " + esc(type) + (param ? " · requires parameter <code>" + esc(param) + "</code>" : "") + (type === "search" ? " · optional <code>q</code> and <code>limit</code>" : type === "list" ? " · optional filters and <code>limit</code>" : "");
    if (READ_TOOL_TYPES.indexOf(type) === -1) {
      hints += "<div class=\"help warn-inline\">Heads up: this tool mutates stored data. Running it here changes the dataset your attendees will see.</div>";
    }
    if (state.testMode === "rest") {
      var sendsBody = ["POST", "PUT", "PATCH"].indexOf(String(endpoint.method).toUpperCase()) !== -1;
      hints += "<div class=\"help\" style=\"margin-top:6px\">Calls <code>" + esc(restUrl(selectedServer(), endpoint)) + "</code>. Path placeholders are filled from the parameters; the rest go in the " + (sendsBody ? "JSON body" : "query string") + ".</div>";
    }
    help.innerHTML = hints;
    var toolValue = selectEl.value;
    if (endpoint.dataset_id) {
      api("/api/datasets/" + endpoint.dataset_id + "/expands", { silentError: true }).then(function (expands) {
        if (!expands || !expands.length) return;
        var helpEl = document.getElementById("tool-help");
        if (!helpEl || !document.getElementById("tool-select") || document.getElementById("tool-select").value !== toolValue) return;
        var rows = expands.map(function (ex) {
          return "<tr><td><code>" + esc(ex.name) + "</code></td><td>" + esc(ex.direction) + "</td><td><span class=\"badge neutral\">" + esc(ex.returns) + "</span></td><td><code>?expand=" + esc(ex.name) + "</code></td></tr>";
        }).join("");
        helpEl.innerHTML += "<div class=\"rel-expand-hints\"><strong>Available expands</strong>" +
          "<div class=\"table-wrap\"><table><thead><tr><th>Name</th><th>Direction</th><th>Returns</th><th>Example</th></tr></thead><tbody>" + rows + "</tbody></table></div></div>";
      });
    }
  }

  function applyToolSearch() {
    var inputEl = document.getElementById("tool-search");
    if (!inputEl) return;
    var query = inputEl.value.toLowerCase();
    var any = false;
    Array.prototype.forEach.call(document.querySelectorAll(".tool-row"), function (row) {
      var match = !query || String(row.dataset.search || "").indexOf(query) !== -1;
      row.hidden = !match;
      if (match) any = true;
    });
    Array.prototype.forEach.call(document.querySelectorAll(".tool-group"), function (group) {
      group.hidden = !Array.prototype.some.call(group.querySelectorAll(".tool-row"), function (row) { return !row.hidden; });
    });
    var empty = document.getElementById("tool-no-results");
    if (empty) empty.hidden = any;
  }

  function updateDatasetPreview() {
    var form = document.querySelector("form[data-form='dataset']");
    var preview = document.getElementById("dataset-preview");
    if (!form || !preview) return;
    try {
      var rows = parseRows(form.elements.rows_text.value, form.elements.format.value);
      var schema = deriveSchema(rows);
      preview.innerHTML = "Detected " + rows.length + " rows." + (rows.length ? schemaHtml(schema) : "");
    } catch (err) {
      preview.innerHTML = "<span class=\"badge bad\">Parse error</span> " + esc(err.message);
    }
  }

  function applyCohortFilter() {
    var inputEl = document.getElementById("cohort-filter");
    if (!inputEl) return;
    var query = inputEl.value.trim().toLowerCase();
    var total = state.cohort.length;
    var visible = 0;
    Array.prototype.forEach.call(document.querySelectorAll(".cohort-row"), function (row) {
      var match = !query || String(row.dataset.search || "").indexOf(query) !== -1;
      row.hidden = !match;
      if (match) visible += 1;
    });
    Array.prototype.forEach.call(document.querySelectorAll(".cohort-handout-row"), function (row) {
      row.hidden = query && String(row.dataset.search || "").indexOf(query) === -1;
    });
    var summary = document.querySelector("[data-cohort-filter-summary]");
    if (summary) summary.textContent = query ? "Filter active: exporting " + visible + " of " + total + " team servers." : "Export includes all " + total + " team servers.";
    var noResults = document.getElementById("cohort-no-results");
    if (noResults) noResults.hidden = visible || !total;
    var handoutEmpty = document.getElementById("cohort-handout-empty");
    if (handoutEmpty) handoutEmpty.hidden = visible || !total;
  }

  function handoutRows() {
    return cohortFilteredServers().map(function (server) {
      return [server.slug || "", cohortMcpUrl(server), cohortRestUrl(server), cohortAuthNote(server)];
    });
  }

  function handoutMarkdown() {
    var lines = ["| Team slug | MCP URL | REST URL | Auth note |", "| --- | --- | --- | --- |"];
    handoutRows().forEach(function (row) {
      lines.push("| " + row.map(markdownCell).join(" | ") + " |");
    });
    return lines.join("\n");
  }

  function markdownCell(value) {
    return String(value == null ? "" : value).replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
  }

  function handoutCsv() {
    var rows = [["Team slug", "MCP URL", "REST URL", "Auth note"]].concat(handoutRows());
    return rows.map(function (row) { return row.map(csvCell).join(","); }).join("\n");
  }

  function csvCell(value) {
    var text = String(value == null ? "" : value);
    return /[",\r\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
  }

  async function copyHandoutMarkdown() {
    if (!handoutRows().length) { toast("No rows to export.", "error"); return; }
    await copyText(handoutMarkdown());
  }

  function downloadHandoutCsv() {
    if (!handoutRows().length) { toast("No rows to export.", "error"); return; }
    var blob = new Blob([handoutCsv()], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = state.cohortFilter.trim() ? "cohort-handout-filtered.csv" : "cohort-handout.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  function printHandout() {
    if (!handoutRows().length) { toast("No rows to print.", "error"); return; }
    window.print();
  }

  function readRulesFromDom() {
    return Array.prototype.map.call(document.querySelectorAll(".rule-card"), function (card) {
      var i = card.dataset.ruleIndex;
      return {
        match_type: document.querySelector("[name='rule_match_type_" + i + "']").value,
        match_value: document.querySelector("[name='rule_match_value_" + i + "']").value,
        response: document.querySelector("[name='rule_response_" + i + "']").value
      };
    });
  }

  async function refreshForServer() {
    await Promise.all([loadDatasets(), loadEndpoints(), loadRelationships()]);
  }

  document.addEventListener("input", function (event) {
    if (event.target.matches("#tool-search")) {
      state.testSearch = event.target.value;
      applyToolSearch();
    }
    if (event.target.matches("#cohort-filter")) {
      state.cohortFilter = event.target.value;
      applyCohortFilter();
    }
    if (event.target.matches("[data-bulk-clone-field]")) updateBulkClonePreview();
    if (event.target.matches("form[data-form='endpoint'] input[name='path']")) updateToolTypePreview();
    if (event.target.matches("form[data-form='dataset'] textarea, form[data-form='dataset'] select")) updateDatasetPreview();
    if (event.target.matches("form[data-form='recipe'] input[name='slug']")) updateRecipePublicCallout();
  });

  document.addEventListener("change", async function (event) {
    if (event.target.matches("[data-action='select-server']")) {
      state.selectedServerId = Number(event.target.value);
      state.selectedDatasetId = null;
      state.editingEndpointId = null;
      state.testResult = null;
      state.testStatus = null;
      state.testCurl = null;
      state.testTool = null;
      state.loading = true;
      render();
      try {
        await refreshForServer();
      } catch (err) {
        if (err.message !== "not authenticated" && !err.toasted) toast(err.message, "error");
      } finally {
        state.loading = false;
        if (state.user) render();
      }
    }
    if (event.target.matches("form[data-form='endpoint'] select[name='method']")) updateToolTypePreview();
    if (event.target.matches("form[data-form='llm'] select[name='mode']")) updateProxyFields();
    if (event.target.matches("#test-mode")) {
      captureTestForm();
      state.testMode = event.target.value;
      render();
    }
    if (event.target.matches("#tool-select")) {
      state.testTool = event.target.value;
      updateToolHelp();
    }
    if (event.target.matches("form[data-form='recipe'] input[name='published']")) updateRecipePublicCallout();
    if (event.target.matches("#recipe-tool-server")) {
      captureRecipeDraft();
      state.recipeToolServerId = Number(event.target.value);
      state.recipeToolName = "";
      await ensureRecipeToolEndpoints();
      render();
    }
    if (event.target.matches("#recipe-tool-name")) state.recipeToolName = event.target.value;
  });

  document.addEventListener("submit", async function (event) {
    event.preventDefault();
    var form = event.target;
    var button = form.querySelector("button[type='submit']");
    setBusy(button, true);
    try {
      if (form.dataset.form === "login") await submitLogin(form);
      if (form.dataset.form === "server") await submitServer(form);
      if (form.dataset.form === "clone-server") await submitClone(form);
      if (form.dataset.form === "bulk-clone-server") await submitBulkClone(form);
      if (form.dataset.form === "cohort-reset") await submitCohortReset(form);
      if (form.dataset.form === "dataset") await submitDataset(form);
      if (form.dataset.form === "dataset-meta") await submitDatasetMeta(form);
      if (form.dataset.form === "rows") await submitRows(form);
      if (form.dataset.form === "endpoint") await submitEndpoint(form);
      if (form.dataset.form === "recipe") await submitRecipe(form);
      if (form.dataset.form === "llm") await submitLlm(form);
      if (form.dataset.form === "tool-call") await submitToolCall(form);
      if (form.dataset.form === "key") await submitKey(form);
      if (form.dataset.form === "relationship") await submitRelationship(form);
    } catch (err) {
      if (err.message !== "not authenticated" && !err.toasted) toast(err.message, "error");
    } finally {
      setBusy(button, false);
    }
  });

  document.addEventListener("click", async function (event) {
    var copy = event.target.closest("[data-copy]");
    if (copy) { copyText(copy.dataset.copy); return; }
    var button = event.target.closest("[data-action]");
    if (!button || button.tagName === "SELECT" || button.tagName === "INPUT") return;
    var action = button.dataset.action;
    try {
      if (action === "logout") { await api("/api/auth/logout", { method: "POST" }); state.user = null; renderLogin(); }
      if (action === "close-modal") { modalRoot.innerHTML = ""; return; }
      if (action === "clone-server") openCloneModal(catalogServerById(button.dataset.id));
      if (action === "bulk-clone-server") openBulkCloneModal(catalogServerById(button.dataset.id));
      if (action === "refresh-cohort") { await loadCohortPage(); render(); }
      if (action === "refresh-cohort-traffic") { await loadCohortTrafficSummary(); render(); }
      if (action === "export-handout-markdown") await copyHandoutMarkdown();
      if (action === "download-handout-csv") downloadHandoutCsv();
      if (action === "print-handout") printHandout();
      if (action === "edit-server") { state.editingServerId = Number(button.dataset.id); render(); }
      if (action === "cancel-server-edit") { state.editingServerId = null; render(); }
      if (action === "delete-server") await deleteItem("server", button.dataset.id, button.dataset.name, "/api/servers/" + button.dataset.id, async function () { await loadServers(); await refreshForServer(); });
      if (action === "select-dataset") { state.selectedDatasetId = Number(button.dataset.id); await loadDatasetDetail(state.selectedDatasetId); render(); }
      if (action === "edit-dataset") { state.editingDatasetId = Number(button.dataset.id); render(); }
      if (action === "cancel-dataset-edit") { state.editingDatasetId = null; render(); }
      if (action === "delete-dataset") await deleteItem("dataset", button.dataset.id, button.dataset.name, "/api/datasets/" + button.dataset.id, async function () { state.selectedDatasetId = null; await loadDatasets(); await loadEndpoints(); });
      if (action === "reset-seed") await confirmPost("Reset dataset", "Reset " + selectedDataset().key + " to its seed rows? Current live rows will be replaced.", "/api/datasets/" + button.dataset.id + "/reset-to-seed");
      if (action === "save-seed") await confirmPost("Save seed", "Overwrite the seed snapshot for " + selectedDataset().key + " with current live rows?", "/api/datasets/" + button.dataset.id + "/save-as-seed");
      if (action === "edit-endpoint") { state.editingEndpointId = Number(button.dataset.id); render(); }
      if (action === "cancel-endpoint-edit") { state.editingEndpointId = null; render(); }
      if (action === "delete-endpoint") await deleteItem("endpoint", button.dataset.id, button.dataset.name, "/api/endpoints/" + button.dataset.id, async function () { await loadEndpoints(); });
      if (action === "new-recipe") { state.selectedRecipeId = null; state.recipeDraft = emptyRecipeDraft(); render(); }
      if (action === "select-recipe") { state.selectedRecipeId = Number(button.dataset.id); await loadRecipeDetail(state.selectedRecipeId); await ensureRecipeToolEndpoints(); render(); }
      if (action === "add-recipe-prompt") { captureRecipeDraft(); state.recipeDraft.example_prompts.push(""); render(); }
      if (action === "remove-recipe-prompt") { captureRecipeDraft(); state.recipeDraft.example_prompts.splice(Number(button.dataset.index), 1); render(); }
      if (action === "add-recipe-destination") { captureRecipeDraft(); state.recipeDraft.destinations.push(""); render(); }
      if (action === "remove-recipe-destination") { captureRecipeDraft(); state.recipeDraft.destinations.splice(Number(button.dataset.index), 1); render(); }
      if (action === "add-recipe-tool") { addRecipeTool(); render(); }
      if (action === "remove-recipe-tool") { captureRecipeDraft(); state.recipeDraft.tools.splice(Number(button.dataset.index), 1); render(); }
      if (action === "delete-recipe") await deleteRecipe(button);
      if (action === "copy-recipe-link") await copyRecipeLink();
      if (action === "open-recipe-page") openRecipePage();
      if (action === "download-recipe-handout") downloadRecipeHandout();
      if (action === "select-test-tool") { captureTestForm(); state.testTool = button.dataset.tool; render(); }
      if (action === "edit-llm") { state.editingLlmId = Number(button.dataset.id); await loadLlmDetail(state.editingLlmId); render(); }
      if (action === "cancel-llm-edit") { state.editingLlmId = null; state.llmRules = []; render(); }
      if (action === "delete-llm") await deleteItem("LLM endpoint", button.dataset.id, button.dataset.name, "/api/llm-endpoints/" + button.dataset.id, async function () { state.editingLlmId = null; await loadLlms(); });
      if (action === "add-rule") { state.llmRules = readRulesFromDom(); state.llmRules.push({ match_type: "always", match_value: "", response: "" }); render(); }
      if (action === "remove-rule") { state.llmRules = readRulesFromDom(); state.llmRules.splice(Number(button.dataset.index), 1); render(); }
      if (action === "rule-up" || action === "rule-down") moveRule(action, Number(button.dataset.index));
      if (action === "save-rules") await saveRules(button);
      if (action === "refresh-traffic") await loadTrafficAndRender();
      if (action === "clear-traffic") await clearTraffic();
      if (action === "toggle-traffic") { state.expandedTraffic = String(state.expandedTraffic) === String(button.dataset.id) ? null : button.dataset.id; render(); }
      if (action === "select-connect-platform") { state.connectPlatform = button.dataset.platform; render(); }
      if (action === "download-swagger") await downloadSwagger(button);
      if (action === "retry-boot") await bootData();
      if (action === "dismiss-key") { state.newKey = null; render(); }
      if (action === "delete-key") await deleteItem("API key", button.dataset.id, button.dataset.name, "/api/keys/" + button.dataset.id, async function () { await loadKeys(); });
      if (action === "delete-relationship") await deleteItem("relationship", button.dataset.id, button.dataset.name, "/api/relationships/" + button.dataset.id, loadRelationships);
      if (action === "validate-relationships") await validateRelationships(button);
      if (action === "ensure-demo-relationships") await ensureDemoRelationships(button);
    } catch (err) {
      if (err.message !== "not authenticated" && !err.toasted) toast(err.message, "error");
    }
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-action='auto-traffic']")) {
      state.autoTraffic = event.target.checked;
      configureTrafficTimer();
    }
  });

  async function submitLogin(form) {
    var data = formData(form);
    state.user = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: data.username, password: data.password }) });
    await bootData();
  }

  async function submitServer(form) {
    var data = formData(form);
    var payload = { slug: data.slug, name: data.name, description: data.description || "", auth_mode: data.auth_mode || "none" };
    if (data.id) await api("/api/servers/" + data.id, { method: "PATCH", body: JSON.stringify(payload) });
    else await api("/api/servers", { method: "POST", body: JSON.stringify(payload) });
    state.editingServerId = null;
    await loadServers();
    render();
    toast("Server saved.", "ok");
  }

  async function submitClone(form) {
    var data = formData(form);
    var payload = { slug: (data.clone_slug || "").trim() };
    if ((data.clone_name || "").trim()) payload.name = data.clone_name.trim();
    try {
      await api("/api/servers/" + encodeURIComponent(data.source_id) + "/clone", { method: "POST", body: JSON.stringify(payload), silentError: true });
      await loadServers();
      await loadCatalog();
      await refreshForServer();
      modalRoot.innerHTML = "";
      render();
      toast("Server cloned.", "ok");
    } catch (err) {
      showCloneError(form, cloneErrorMessage(err, false));
    }
  }

  async function submitBulkClone(form) {
    var data = formData(form);
    var payload = {
      prefix: (data.prefix || "").trim(),
      count: Number.parseInt(data.count, 10),
      start: Number.parseInt(data.start || 1, 10)
    };
    try {
      await api("/api/servers/" + encodeURIComponent(data.source_id) + "/bulk-clone", { method: "POST", body: JSON.stringify(payload), silentError: true });
      await loadServers();
      await loadCatalog();
      await refreshForServer();
      modalRoot.innerHTML = "";
      render();
      toast("Servers cloned.", "ok");
    } catch (err) {
      showCloneError(form, cloneErrorMessage(err, true));
    }
  }

  async function submitCohortReset(form) {
    if (state.cohortResetting) return;
    if (!canWrite()) throw new Error("Readonly users cannot mutate data.");
    var data = formData(form);
    var prefix = (data.prefix || "").trim();
    var target = prefix ? "servers whose slug starts with \"" + prefix + "\"" : "ALL servers";
    var message = "Restore " + target + " from seed rows, discarding any trainee changes in live rows.";
    if (!await confirmModal("Reset training environment", message, "Reset")) return;
    state.cohortResetting = true;
    try {
      var result = await api("/api/servers/reset-all-to-seed", { method: "POST", body: JSON.stringify({ prefix: prefix || null }) });
      await loadCohortPage();
      render();
      if (metricCount(result && result.server_count)) toast("Reset " + metricCount(result.server_count) + " servers, " + metricCount(result.dataset_count) + " datasets, " + metricCount(result.row_count) + " rows.", "ok");
      else toast(prefix ? "Nothing matched that prefix." : "No servers matched.", "ok");
    } finally {
      state.cohortResetting = false;
    }
  }

  async function submitDataset(form) {
    var data = formData(form);
    var rows = parseRows(data.rows_text, data.format);
    await api("/api/servers/" + selectedServer().id + "/datasets", { method: "POST", body: JSON.stringify({ key: data.key, id_field: data.id_field || "id", rows: rows }) });
    await loadDatasets();
    render();
    toast("Dataset created.", "ok");
  }

  async function submitDatasetMeta(form) {
    var data = formData(form);
    await api("/api/datasets/" + data.id, { method: "PATCH", body: JSON.stringify({ key: data.key, id_field: data.id_field || "id" }) });
    state.editingDatasetId = null;
    await loadDatasets();
    render();
    toast("Dataset metadata saved.", "ok");
  }

  async function submitRows(form) {
    var data = formData(form);
    var rows = JSON.parse(data.rows);
    if (!Array.isArray(rows)) throw new Error("Rows must be a JSON array.");
    await api("/api/datasets/" + data.id + "/rows", { method: "PUT", body: JSON.stringify({ rows: rows }) });
    await loadDatasetDetail(data.id);
    await loadDatasets();
    render();
    toast("Rows saved.", "ok");
  }

  async function submitEndpoint(form) {
    var data = formData(form);
    var type = inferToolType(data.method, data.path);
    if (!type) throw new Error("Unsupported endpoint shape. Fix path/method before saving.");
    var fields = (type === "list" || type === "search") && data.summary_fields ? data.summary_fields.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];
    var payload = { path: data.path, method: data.method, tool_name: data.tool_name, description: data.description || "", dataset_id: Number(data.dataset_id), summary_fields: fields };
    if (data.id) await api("/api/endpoints/" + data.id, { method: "PATCH", body: JSON.stringify(payload) });
    else await api("/api/servers/" + selectedServer().id + "/endpoints", { method: "POST", body: JSON.stringify(payload) });
    state.editingEndpointId = null;
    await loadEndpoints();
    render();
    toast("Endpoint saved.", "ok");
  }

  function addRecipeTool() {
    var serverSelect = document.getElementById("recipe-tool-server");
    var toolSelect = document.getElementById("recipe-tool-name");
    var draft = captureRecipeDraft();
    if (!serverSelect || !toolSelect || !toolSelect.value) {
      toast("Select a server and tool first.", "error");
      return;
    }
    var serverId = Number(serverSelect.value);
    var toolName = toolSelect.value;
    if (draft.tools.some(function (tool) { return Number(tool.server_id) === serverId && tool.tool_name === toolName; })) {
      toast("That tool is already in this recipe.", "error");
      return;
    }
    draft.tools.push({ server_id: serverId, tool_name: toolName, server_name: recipeServerName(serverId) });
    state.recipeDraft = draft;
  }

  async function submitRecipe(form) {
    var data = formData(form);
    var payload = recipePayloadFromForm(form);
    var saved;
    if (data.id) {
      var tools = payload.tools;
      delete payload.tools;
      saved = await api("/api/recipes/" + data.id, { method: "PATCH", body: JSON.stringify(payload) });
      saved = await api("/api/recipes/" + saved.id + "/tools", { method: "PUT", body: JSON.stringify({ tools: tools }) });
    } else {
      saved = await api("/api/recipes", { method: "POST", body: JSON.stringify(payload) });
    }
    state.selectedRecipeId = saved.id;
    state.recipeDraft = recipeToDraft(saved);
    await loadRecipes();
    render();
    toast("Recipe saved.", "ok");
  }

  async function deleteRecipe(button) {
    if (!button.dataset.id) return;
    if (!await confirmModal("Delete recipe", "Delete " + button.dataset.name + "? This cannot be undone from this UI.", "Delete")) return;
    await api("/api/recipes/" + button.dataset.id, { method: "DELETE" });
    state.selectedRecipeId = null;
    state.recipeDraft = null;
    await loadRecipes();
    render();
    toast("Recipe deleted.", "ok");
  }

  async function copyRecipeLink() {
    var draft = captureRecipeDraft();
    await copyText(recipeUrl(draft.slug));
  }

  function openRecipePage() {
    var draft = captureRecipeDraft();
    window.open(recipeUrl(draft.slug), "_blank", "noopener");
  }

  async function downloadSwagger(button) {
    var spec = await api("/api/servers/" + encodeURIComponent(button.dataset.id) + "/swagger");
    var blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = (button.dataset.slug || "server") + "-openapi.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("OpenAPI file downloaded. Share this file with attendees.", "ok");
  }

  function downloadRecipeHandout() {
    var draft = captureRecipeDraft();
    if (!draft.id) return;
    window.location.href = "/api/recipes/" + encodeURIComponent(draft.id) + "/handout?download=true";
  }

  async function submitLlm(form) {
    var data = formData(form);
    var payload = { slug: data.slug, name: data.name, description: data.description || "", mode: data.mode, model_name: data.model_name || "playground-model", auth_mode: data.auth_mode || "none" };
    if (data.mode === "proxy") {
      payload.upstream_url = data.upstream_url || null;
      payload.upstream_key = data.upstream_key || null;
      payload.upstream_deployment = data.upstream_deployment || null;
      payload.system_prompt = data.system_prompt || null;
    }
    if (data.clear_upstream_key) payload.clear_upstream_key = true;
    var saved;
    if (data.id) saved = await api("/api/llm-endpoints/" + data.id, { method: "PATCH", body: JSON.stringify(payload) });
    else saved = await api("/api/llm-endpoints", { method: "POST", body: JSON.stringify(payload) });
    if (data.mode === "mock") {
      state.llmRules = readRulesFromDom();
      if (state.llmRules.length) await api("/api/llm-endpoints/" + saved.id + "/responses", { method: "PUT", body: JSON.stringify(state.llmRules) });
    }
    state.editingLlmId = null;
    state.llmRules = [];
    await loadLlms();
    render();
    toast("LLM endpoint saved.", "ok");
  }

  async function submitToolCall(form) {
    var data = formData(form);
    var params = JSON.parse(data.params || "{}");
    var server = selectedServer();
    state.testMode = data.mode === "executor" ? "executor" : "rest";
    state.testTool = data.tool_name;
    state.testParams = data.params || "{}";
    if (data.api_key != null) state.testApiKey = data.api_key;
    var started = performance.now();
    if (state.testMode === "rest") {
      var endpoint = state.endpoints.find(function (e) { return e.tool_name === data.tool_name; });
      if (!endpoint) throw new Error("Unknown tool for this server.");
      var request = buildRestRequest(server, endpoint, params);
      var options = { method: request.method, credentials: "omit", headers: {} };
      if (state.testApiKey) options.headers["X-API-Key"] = state.testApiKey;
      if (request.body != null) {
        options.body = request.body;
        options.headers["Content-Type"] = "application/json";
      }
      var response;
      try {
        response = await fetch(request.url, options);
      } catch (err) {
        toast("Network error: " + err.message, "error");
        err.toasted = true;
        throw err;
      }
      var text = await response.text();
      state.testElapsed = Math.round(performance.now() - started);
      state.testStatus = response.status;
      state.testCurl = curlFor(request, state.testApiKey);
      try { state.testResult = text ? JSON.parse(text) : null; } catch (_err) { state.testResult = text; }
    } else {
      state.testCurl = null;
      var result = await api("/api/servers/" + encodeURIComponent(server.slug) + "/tools/" + encodeURIComponent(data.tool_name) + "/call", { method: "POST", body: JSON.stringify(params) });
      state.testElapsed = Math.round(performance.now() - started);
      state.testStatus = 200;
      state.testResult = result;
    }
    render();
  }

  async function submitKey(form) {
    var data = formData(form);
    state.newKey = await api("/api/keys", { method: "POST", body: JSON.stringify({ label: data.label, scope: data.scope }) });
    await loadKeys();
    render();
  }

  async function deleteItem(kind, id, name, url, refresh) {
    if (!await confirmModal("Delete " + kind, "Delete " + name + "? This cannot be undone from this UI.", "Delete")) return;
    await api(url, { method: "DELETE" });
    await refresh();
    render();
    toast(kind + " deleted.", "ok");
  }

  async function submitRelationship(form) {
    var data = formData(form);
    var body = {
      name: data.name,
      source_dataset_id: Number(data.source_dataset_id),
      source_field: data.source_field,
      target_dataset_id: Number(data.target_dataset_id),
      target_field: data.target_field,
      relation_type: data.relation_type,
      expand_name: data.expand_name,
      inverse_expand_name: data.inverse_expand_name || null,
      required: !!data.required,
      description: data.description || null
    };
    var server = selectedServer();
    await api("/api/servers/" + server.id + "/relationships", { method: "POST", body: JSON.stringify(body) });
    await loadRelationships();
    render();
    toast("Relationship created.", "ok");
  }

  async function validateRelationships(button) {
    var server = selectedServer();
    if (!server) return;
    setBusy(button, true);
    try {
      state.validateResult = await api("/api/servers/" + server.id + "/relationships/validate");
      render();
    } finally {
      setBusy(button, false);
    }
  }

  async function ensureDemoRelationships(button) {
    setBusy(button, true);
    try {
      var result = await api("/api/relationships/ensure-demo", { method: "POST", body: JSON.stringify({}) });
      await loadRelationships();
      render();
      toast("Demo relationships: " + result.relationships_created + " created, " + result.skipped_existing + " skipped, " + result.servers_touched + " servers touched.", "ok");
    } finally {
      setBusy(button, false);
    }
  }

  async function confirmPost(title, message, url) {
    if (!await confirmModal(title, message, "Continue")) return;
    var detail = await api(url, { method: "POST" });
    if (detail && detail.id) await loadDatasetDetail(detail.id);
    await loadDatasets();
    render();
    toast("Done.", "ok");
  }

  function moveRule(action, index) {
    state.llmRules = readRulesFromDom();
    var target = action === "rule-up" ? index - 1 : index + 1;
    if (target < 0 || target >= state.llmRules.length) return;
    var tmp = state.llmRules[index];
    state.llmRules[index] = state.llmRules[target];
    state.llmRules[target] = tmp;
    render();
  }

  async function saveRules(button) {
    setBusy(button, true);
    try {
      state.llmRules = readRulesFromDom();
      await api("/api/llm-endpoints/" + state.editingLlmId + "/responses", { method: "PUT", body: JSON.stringify(state.llmRules) });
      await loadLlmDetail(state.editingLlmId);
      render();
      toast("Rules saved.", "ok");
    } finally { setBusy(button, false); }
  }

  async function loadTrafficAndRender() {
    state.traffic = await api("/api/traffic");
    render();
  }

  async function clearTraffic() {
    if (!await confirmModal("Clear traffic", "Clear all recorded traffic logs?", "Clear")) return;
    await api("/api/traffic", { method: "DELETE" });
    state.traffic = [];
    render();
    toast("Traffic cleared.", "ok");
  }

  async function loadKeys() {
    state.keys = await api("/api/keys");
  }

  // render() replaces the whole app element, so an unattended refresh would wipe out
  // whatever the trainer is typing mid-demo. Hold off while a field has focus.
  function isEditingAField() {
    var active = document.activeElement;
    if (!active) return false;
    var tag = active.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || active.isContentEditable;
  }

  function configureTrafficTimer() {
    if (state.trafficTimer) clearInterval(state.trafficTimer);
    state.trafficTimer = null;
    if (state.autoTraffic) {
      state.trafficTimer = setInterval(function () {
        api("/api/traffic", { silentError: true }).then(function (rows) {
          state.traffic = rows;
          if (state.tab === "traffic" && !isEditingAField()) render();
        }).catch(function () {});
      }, 3000);
    }
  }

  async function copyText(value) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
      else {
        var t = document.createElement("textarea");
        t.value = value;
        document.body.appendChild(t);
        t.select();
        document.execCommand("copy");
        t.remove();
      }
      toast("Copied.", "ok");
    } catch (err) {
      toast("Copy failed: " + err.message, "error");
    }
  }

  // Every read costs about a second against Azure Files, so the tab has to repaint
  // before the data arrives. Without this the trainer clicks and the screen sits
  // there, and a failed load used to leave state.tab pointing at a tab the DOM
  // never rendered.
  document.addEventListener("click", async function (event) {
    var tab = event.target.closest("[data-tab]");
    if (!tab) return;
    var previous = state.tab;
    state.tab = tab.dataset.tab;
    state.loading = true;
    render();
    try {
      if (state.tab === "catalog") { await loadCatalog(); await loadRecipes(); }
      if (state.tab === "recipes") await loadRecipes();
      if (state.tab === "cohort") await loadCohortPage();
      if (state.tab === "datasets") { await loadDatasets(); await loadRelationships(); }
      if (state.tab === "endpoints") await refreshForServer();
      if (state.tab === "llm") await loadLlms();
      if (state.tab === "traffic") state.traffic = await api("/api/traffic");
      if (state.tab === "keys") await loadKeys();
    } catch (err) {
      state.tab = previous;
      if (err.message !== "not authenticated" && !err.toasted) toast(err.message, "error");
    } finally {
      state.loading = false;
      if (state.user) render();
    }
  });

  init();
}());
