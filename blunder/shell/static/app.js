"use strict";

const state = {
  token: "",
  workspace: null,
  jobs: [],
  activeJob: null,
  view: "work",
  paletteItems: [],
  searchTimer: null,
  searchMode: "names",
  selectedCheckpoint: null,
  missionBranches: [],
  missionBranchTimer: null,
  selectedMissionBranch: null,
  missionPrograms: [],
  capabilities: null,
  toastTimer: null,
  editorPath: null,
  editorSha256: null,
  editorSaveConfirmation: null,
  editorAnalysisTimer: null,
};

const $ = (id) => document.getElementById(id);
const all = (selector) => Array.from(document.querySelectorAll(selector));
const text = (id, value) => { const node = $(id); if (node) node.textContent = value ?? "—"; };
const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));
const display = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);

function bootstrapToken() {
  const url = new URL(window.location.href);
  const queryToken = url.searchParams.get("token");
  if (queryToken) {
    sessionStorage.setItem("blunder-shell-token", queryToken);
    url.searchParams.delete("token");
    history.replaceState({}, "", url.pathname + url.hash);
  }
  state.token = queryToken || sessionStorage.getItem("blunder-shell-token") || "";
}

async function api(path, options = {}) {
  const headers = { "X-Blunder-Token": state.token, ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function toast(message, tone = "good") {
  clearTimeout(state.toastTimer);
  text("toastText", message);
  text("toastIcon", tone === "bad" ? "!" : "✓");
  $("toastIcon").style.color = tone === "bad" ? "var(--danger)" : "var(--lime)";
  $("toast").classList.remove("hidden");
  state.toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 4800);
}

function setConnection(connected) {
  text("connectionLabel", connected ? "Shell connected" : "Shell disconnected");
  document.querySelector(".connection-dot").style.background = connected ? "var(--lime)" : "var(--danger)";
}

function setView(name) {
  state.view = name;
  all("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === name));
  all("[data-view-target]").forEach((node) => node.classList.toggle("active", node.dataset.viewTarget === name));
  location.hash = name === "work" ? "" : name;
  if (name === "missions") loadMissions();
  if (name === "capabilities") loadCapabilities();
  if (name === "frontier") loadFrontier();
  if (name === "product") loadProductStudio();
  if (name === "creation") loadCreationForge();
  if (name === "kernel") loadKernel();
  if (name === "evolve") loadCCOS();
}

function meter(id, percent) { $(id).style.width = `${clamp(percent, 0, 100)}%`; }

function renderWorkspace(payload) {
  state.workspace = payload;
  const planning = payload.planning || {};
  const evolution = payload.evolution || {};
  const provider = payload.provider || {};
  const runtime = payload.runtime || {};
  const queue = payload.queue || {};

  text("planningScore", planning.score === null || planning.score === undefined ? "—" : `${planning.score}`);
  text("planningVerdict", display(planning.verdict, "Not run").replaceAll("_", " "));
  meter("planningMeter", planning.score || 0);
  text("pressureScore", evolution.pressure ? `${Number(evolution.pressure).toFixed(2)}×` : "—");
  text("selectedFamily", evolution.selectedFamily ? `${evolution.selectedFamily} selected` : "No plan loaded");
  meter("pressureMeter", (evolution.pressure || 0) / 3 * 100);
  text("candidateBudget", evolution.candidateBudget);
  meter("budgetMeter", (evolution.candidateBudget || 0) / 4 * 100);
  text("championScore", evolution.championFitness === null || evolution.championFitness === undefined ? "—" : Number(evolution.championFitness).toFixed(2));
  meter("championMeter", (evolution.championFitness || 0) * 100);

  text("modelLabel", provider.model);
  text("providerModel", provider.model);
  text("providerCalls", provider.calls);
  text("providerFailures", provider.failures);
  text("providerState", provider.enabled === true ? "Ready" : provider.enabled === false ? "Gated" : "Unknown");
  text("runtimeState", runtime.active === true ? "Active" : runtime.active === false ? "Idle" : "Unknown");
  text("pidState", runtime.pidValid === true ? "Valid" : runtime.pidValid === false ? "Not active" : "—");
  text("lastAdvice", display(runtime.lastAction, "No current advice").replaceAll("_", " "));
  text("queueTotal", queue.total);
  renderQueue(queue.counts || {});
  text("systemStatus", planning.score >= 85 && evolution.status === "PASS" ? "Proof green" : "Review state");
  $("systemStatus").classList.toggle("good", planning.score >= 85 && evolution.status === "PASS");

  text("evolveFamily", evolution.selectedFamily);
  text("evolvePressure", evolution.pressure ? `${Number(evolution.pressure).toFixed(2)}×` : "—");
  text("evolveBudget", evolution.candidateBudget);
  text("evolveChampion", evolution.championFitness === null || evolution.championFitness === undefined ? "—" : Number(evolution.championFitness).toFixed(2));
  text("cooldownLabel", (evolution.cooledFamilies || []).length ? `Cooling: ${evolution.cooledFamilies.join(", ")}` : "No cooldowns");
  renderRanking(evolution.rankedFamilies || []);
  renderArchive(evolution.archive || []);
}

function renderQueue(counts) {
  const root = $("queueBreakdown");
  root.replaceChildren();
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    const row = document.createElement("div"); row.className = "breakdown-row"; row.textContent = "No queue evidence"; root.append(row); return;
  }
  entries.slice(0, 5).forEach(([name, count]) => {
    const row = document.createElement("div"); row.className = "breakdown-row";
    const label = document.createElement("span"); label.textContent = name.replaceAll("_", " ");
    const value = document.createElement("strong"); value.textContent = String(count);
    row.append(label, value); root.append(row);
  });
}

function renderRanking(rows) {
  const root = $("familyRanking"); root.replaceChildren();
  if (!rows.length) { const p = document.createElement("p"); p.className = "subtle-label"; p.textContent = "Run Refresh workspace to build the portfolio."; root.append(p); return; }
  const max = Math.max(...rows.map((row) => Math.max(0, Number(row.selection_score) || 0)), 1);
  rows.forEach((row) => {
    const item = document.createElement("div"); item.className = "family-row";
    const name = document.createElement("span"); name.className = "family-name"; name.textContent = row.family;
    const bar = document.createElement("span"); bar.className = "family-bar"; const fill = document.createElement("i"); fill.style.width = `${Math.max(2, Number(row.selection_score) / max * 100)}%`; bar.append(fill);
    const score = document.createElement("span"); score.className = "family-score"; score.textContent = Number(row.selection_score).toFixed(2);
    const familyState = document.createElement("span"); familyState.className = `family-state${row.cooled_down ? " cool" : ""}`; familyState.textContent = row.cooled_down ? "cooling" : `${row.promotions}/${row.attempts}`;
    item.append(name, bar, score, familyState); root.append(item);
  });
}

function renderArchive(rows) {
  const root = $("eliteArchive"); root.replaceChildren();
  if (!rows.length) { const p = document.createElement("p"); p.className = "subtle-label"; p.textContent = "No elite archive loaded."; root.append(p); return; }
  rows.forEach((row) => {
    const card = document.createElement("article"); card.className = "elite-card";
    const family = document.createElement("span"); family.textContent = row.family;
    const fitness = document.createElement("strong"); fitness.textContent = Number(row.fitness || 0).toFixed(2);
    const candidate = document.createElement("small"); candidate.textContent = row.candidate || "No candidate";
    card.append(family, fitness, candidate); root.append(card);
  });
}

function renderCCOS(payload) {
  const authority = payload.authority || {};
  text("ccosRuntimeState", payload.status);
  text("ccosGeneration", payload.generation);
  text("ccosHead", payload.headDigest ? String(payload.headDigest).slice(0, 12) : "Empty");
  text("ccosProviderCalls", payload.providerCalls);
  text("ccosActivation", authority.automaticActivationAllowed === false ? "Closed" : "Blocked");
  $("ccosRuntimeState").classList.toggle("good", payload.status === "OPERATIONAL_OBSERVATION_ONLY");
}

async function loadCCOS() {
  try {
    const [payload, retention, recovery] = await Promise.all([api("/api/ccos"), api("/api/ccos/retention"), api("/api/ccos/recovery")]);
    renderCCOS(payload);
    text("ccosResult", proofJson({ headDigest: payload.headDigest, indexDigest: payload.indexDigest, counts: payload.counts, retention, recovery, authority: payload.authority }));
  } catch (error) { text("ccosRuntimeState", "BLOCKED"); toast(error.message, "bad"); }
}

async function appendCCOSRecord() {
  let payload;
  try { payload = JSON.parse($("ccosRecordPayload").value); }
  catch (error) { toast(`Evidence payload is invalid JSON: ${error.message}`, "bad"); return; }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") { toast("Evidence payload must be a JSON object.", "bad"); return; }
  try {
    const record = await api("/api/ccos/records", { method: "POST", body: JSON.stringify({ kind: $("ccosRecordKind").value, payload }) });
    text("ccosResult", proofJson(record)); toast("Immutable CCOS evidence recorded without authority."); await loadCCOS();
  } catch (error) { toast(error.message, "bad"); }
}

async function runCCOSCanary() {
  try {
    const body = { activationConfirmation: "SIMULATE_CCOS_ACTIVATION_GATE", rollbackConfirmation: "VERIFY_CCOS_ROLLBACK_GATE" };
    const report = await api("/api/ccos/canary", { method: "POST", body: JSON.stringify(body) });
    text("ccosResult", proofJson(report)); toast(`CCOS canary: ${report.status}.`, report.status === "PASS" ? "good" : "bad"); await loadCCOS();
  } catch (error) { toast(error.message, "bad"); }
}

async function recoverCCOS() {
  try {
    const report = await api("/api/ccos/recover", { method: "POST", body: JSON.stringify({ confirmation: $("ccosRecoveryConfirmation").value }) });
    text("ccosResult", proofJson(report)); $("ccosRecoveryConfirmation").value = ""; toast("CCOS index restored to the last verified generation."); await loadCCOS();
  } catch (error) { toast(error.message, "bad"); }
}

async function loadWorkspace(force = false) {
  $("refreshButton").disabled = true;
  try {
    renderWorkspace(await api(`/api/state${force ? "?force=1" : ""}`));
    setConnection(true);
  } catch (error) {
    setConnection(false); toast(error.message, "bad");
  } finally { $("refreshButton").disabled = false; }
}

async function startAction(action, extra = {}) {
  try {
    const job = await api("/api/actions", { method: "POST", body: JSON.stringify({ action, ...extra }) });
    state.activeJob = job.id; renderJob(job); pollJobs();
    toast(`${job.label} started.`);
  } catch (error) { toast(error.message, "bad"); }
}

function renderJob(job) {
  if (!job) return;
  $("emptyActivity").classList.add("hidden"); $("jobView").classList.remove("hidden");
  text("jobLabel", job.label); text("jobStatus", job.status); text("jobTime", job.startedAt ? new Date(job.startedAt).toLocaleTimeString() : "");
  text("jobOutput", job.output || (job.error ? job.error : "Waiting for output…"));
  $("jobOutput").scrollTop = $("jobOutput").scrollHeight;
  $("jobStatus").className = `status-chip ${job.status === "completed" ? "good" : job.status === "failed" || job.status === "cancelled" ? "bad" : ""}`;
  const active = ["queued", "running"].includes(job.status); $("stopJobButton").classList.toggle("hidden", !active);
  $("runTaskButton").disabled = active;
  if (!active && state.activeJob === job.id) { state.activeJob = null; loadWorkspace(true); }
}

async function pollJobs() {
  try {
    const payload = await api("/api/jobs"); state.jobs = payload.jobs || [];
    const current = state.activeJob ? state.jobs.find((job) => job.id === state.activeJob) : state.jobs[0];
    if (current) renderJob(current);
    if (current && ["queued", "running"].includes(current.status)) setTimeout(pollJobs, 1100);
  } catch (error) { toast(error.message, "bad"); }
}

async function cancelActiveJob() {
  if (!state.activeJob) return;
  try { renderJob(await api(`/api/jobs/${state.activeJob}/cancel`, { method: "POST", body: "{}" })); toast("Action stopped."); }
  catch (error) { toast(error.message, "bad"); }
}

async function runTask() {
  const task = $("taskInput").value.trim();
  await startAction("run_task", { task, providerApproved: $("providerApproval").checked });
}

async function buildBriefing() {
  const task = $("taskInput").value.trim();
  $("briefTaskButton").disabled = true;
  try {
    const payload = await api("/api/briefing", { method: "POST", body: JSON.stringify({ task }) });
    const context = payload.context || {}; const contract = payload.contract || {};
    text("briefingSources", context.sourceCount); text("briefingContext", `${Number(context.utilizationPercent || 0).toFixed(1)}%`);
    text("briefingBytes", `${Number(context.bytes || 0).toLocaleString()} local bytes`); text("briefingFiles", contract.maximumChangedFiles);
    const gates = $("briefingGates"); gates.replaceChildren();
    (payload.gates || []).forEach((gate) => {
      const row = document.createElement("div"); row.className = "gate-row";
      const name = document.createElement("span"); name.textContent = gate.name;
      const status = document.createElement("strong"); status.textContent = gate.status; row.append(name, status); gates.append(row);
    });
    const evidence = $("briefingEvidence"); evidence.replaceChildren();
    (context.sources || []).slice(0, 6).forEach((source) => {
      const row = document.createElement("button"); row.type = "button"; row.className = "evidence-row";
      const path = document.createElement("strong"); path.textContent = source.path;
      const detail = document.createElement("small"); detail.textContent = `${source.termHitCount} term hits · ${source.lines} lines`;
      row.append(path, detail); row.addEventListener("click", () => { setView("files"); openFile(source.path, row); }); evidence.append(row);
    });
    $("briefingEmpty").classList.add("hidden"); $("briefingContent").classList.remove("hidden");
    toast("Evidence briefing ready. No provider call spent.");
  } catch (error) { toast(error.message, "bad"); }
  finally { $("briefTaskButton").disabled = false; }
}

function renderPlugins(plugins) {
  const root = $("pluginGrid"); root.replaceChildren();
  plugins.forEach((plugin) => {
    const card = document.createElement("article"); card.className = "plugin-card";
    const head = document.createElement("div"); const name = document.createElement("strong"); name.textContent = plugin.name;
    const version = document.createElement("span"); version.textContent = `v${plugin.version}`; head.append(name, version);
    const description = document.createElement("p"); description.textContent = plugin.description;
    const stats = document.createElement("small"); stats.textContent = `${plugin.skills.length} skills · ${plugin.tools.length} tools · ${plugin.hooks.length} hooks`;
    const hash = document.createElement("code"); hash.textContent = `SHA ${plugin.manifest_hash.slice(0, 12)}`;
    card.append(head, description, stats, hash); root.append(card);
  });
}

async function loadCapabilities() {
  try {
    const payload = await api("/api/capabilities"); state.capabilities = payload; const counts = payload.counts || {};
    text("pluginCount", counts.plugins); text("skillCount", counts.skills); text("toolCount", counts.tools); text("hookCount", counts.hooks);
    renderPlugins(payload.plugins || []);
    if (!$("capabilityTaskInput").value && $("taskInput").value) $("capabilityTaskInput").value = $("taskInput").value;
  } catch (error) { toast(error.message, "bad"); }
}

function renderCapabilityRoute(payload) {
  const skills = $("routedSkills"); skills.replaceChildren();
  (payload.skills || []).forEach((skill) => {
    const card = document.createElement("article"); card.className = "routed-skill";
    const score = document.createElement("span"); score.textContent = String(skill.score);
    const copy = document.createElement("div"); const name = document.createElement("strong"); name.textContent = skill.name;
    const detail = document.createElement("small"); detail.textContent = `${skill.context_mode} · ${skill.workflow.length} proof steps`; copy.append(name, detail);
    card.append(score, copy); skills.append(card);
  });
  const tools = $("routedTools"); tools.replaceChildren();
  (payload.tools || []).forEach((tool) => {
    const card = document.createElement("article"); card.className = "routed-tool";
    const copy = document.createElement("div"); const name = document.createElement("strong"); name.textContent = tool.name;
    const detail = document.createElement("small"); detail.textContent = tool.description; copy.append(name, detail); card.append(copy);
    if (tool.action_id) {
      const run = document.createElement("button"); run.type = "button"; run.textContent = "Run"; run.addEventListener("click", () => invokeCapabilityTool(tool.tool_id)); card.append(run);
    } else {
      const native = document.createElement("span"); native.textContent = "IDE"; card.append(native);
    }
    tools.append(card);
  });
  text("routedPermissions", (payload.permissions || []).join(" · "));
  $("capabilityRoute").classList.remove("hidden");
}

async function routeCapabilities() {
  const task = $("capabilityTaskInput").value.trim() || $("taskInput").value.trim();
  $("routeCapabilitiesButton").disabled = true;
  try {
    const payload = await api("/api/capabilities/route", { method: "POST", body: JSON.stringify({ task }) });
    renderCapabilityRoute(payload); toast(`${payload.skills.length} skills routed through ${payload.tools.length} bounded tools.`);
  } catch (error) { toast(error.message, "bad"); }
  finally { $("routeCapabilitiesButton").disabled = false; }
}

async function invokeCapabilityTool(toolId) {
  try {
    const job = await api("/api/capabilities/invoke", { method: "POST", body: JSON.stringify({ toolId }) });
    state.activeJob = job.id; renderJob(job); setView("work"); pollJobs(); toast(`${job.label} started through the capability fabric.`);
  } catch (error) { toast(error.message, "bad"); }
}

function proofJson(value) { return JSON.stringify(value, null, 2); }

async function loadFrontier() {
  try {
    const payload = await api("/api/frontier"); const surfaces = payload.surfaces || {}; const campaign = surfaces.missionCampaigns || {};
    text("frontierIntent", surfaces.intentFirewall);
    text("frontierCampaign", campaign.status || "NO_PROGRAMS");
    text("frontierSelfCoding", surfaces.selfCoding);
    text("frontierRelease", surfaces.releaseGovernance);
  } catch (error) { toast(error.message, "bad"); }
}

async function runDiscovery() {
  const question = $("discoveryQuestion").value.trim();
  $("runDiscoveryButton").disabled = true;
  try {
    const payload = await api("/api/discovery/brief", { method: "POST", body: JSON.stringify({ question }) });
    text("discoveryResult", proofJson(payload)); toast(`${payload.evidenceCount} local sources shaped ${payload.hypothesisCount} hypotheses.`);
  } catch (error) { toast(error.message, "bad"); }
  finally { $("runDiscoveryButton").disabled = false; }
}

async function findReferences() {
  try {
    const payload = await api(`/api/editor/references?symbol=${encodeURIComponent($("referenceSymbol").value.trim())}`);
    text("languageResult", proofJson(payload)); toast(`${payload.count} semantic occurrences found.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function findCompletions() {
  try {
    const payload = await api(`/api/editor/completions?prefix=${encodeURIComponent($("completionPrefix").value.trim())}`);
    text("languageResult", proofJson(payload)); toast(`${payload.count} repository-aware completions found.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function loadWorkspaceDiagnostics() {
  try {
    const payload = await api("/api/editor/workspace-diagnostics"); text("languageResult", proofJson(payload));
    toast(`${payload.filesAnalyzed} Python files analyzed without mutation.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function preflightSelfCoding() {
  try {
    const payload = await api("/api/self-coding/preflight", { method: "POST", body: JSON.stringify({ task: $("selfCodingTask").value.trim(), targetPath: $("selfCodingTarget").value.trim() }) });
    text("selfCodingResult", proofJson(payload)); text("selfCodingBadge", payload.status); toast(`Self-coding: ${payload.status}.`, payload.status.startsWith("AUTHORIZED") ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
}

function separatedValues(value, separator) { return value.split(separator).map((item) => item.trim()).filter(Boolean); }

async function loadProductStudio() {
  try {
    const payload = await api("/api/product"); const surfaces = payload.surfaces || {}; const polyglot = surfaces.polyglot || {};
    text("productBlueprintState", surfaces.appBlueprint);
    text("productVisualState", surfaces.visualJudgment);
    text("productDeviceState", surfaces.deviceMatrix);
    text("productPolyglotState", polyglot.status);
    text("productPolyglotDetail", `${polyglot.files || 0} files / ${Object.keys(polyglot.languages || {}).length} languages`);
  } catch (error) { toast(error.message, "bad"); }
}

async function compileProductBlueprint() {
  const payload = {
    productName: $("productName").value.trim(),
    goal: $("productGoal").value.trim(),
    audience: separatedValues($("productAudience").value, ","),
    platforms: separatedValues($("productPlatforms").value, ",").map((item) => item.toLowerCase()),
    capabilities: separatedValues($("productCapabilities").value, "\n"),
    entities: separatedValues($("productEntities").value, ","),
    constraints: [],
  };
  $("compileBlueprintButton").disabled = true;
  try {
    const report = await api("/api/product/blueprint", { method: "POST", body: JSON.stringify(payload) });
    text("productBlueprintResult", proofJson(report)); toast(`${report.surfaces.length} product surfaces and ${report.journeys.length} journeys compiled.`);
  } catch (error) { toast(error.message, "bad"); }
  finally { $("compileBlueprintButton").disabled = false; }
}

async function compileProductDesignSystem() {
  const payload = {
    name: $("designSystemName").value.trim(), background: $("designBackground").value, surface: $("designSurface").value,
    text: $("designText").value, muted: $("designMuted").value, primary: $("designPrimary").value,
    accent: $("designAccent").value, danger: $("designDanger").value, focus: $("designFocus").value,
    typeBasePx: 16, typeRatio: 1.25, spacingBasePx: 4, radiusBasePx: 4,
  };
  try {
    const report = await api("/api/product/design-system", { method: "POST", body: JSON.stringify(payload) });
    text("designSystemResult", proofJson(report)); toast(`Design system: ${report.status}.`, report.status === "PASS" ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
}

async function auditProductProject(domain) {
  try {
    const report = await api("/api/product/project-audit", { method: "POST", body: JSON.stringify({ domain }) });
    text("productLabResult", proofJson(report)); toast(`${domain} lab: ${report.status}.`, report.status.startsWith("READY") ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
}

async function showDeviceMatrix() {
  try {
    const report = await api("/api/product/device-matrix", { method: "POST", body: JSON.stringify({ locale: "en-US", categories: [] }) });
    text("productLabResult", proofJson(report)); toast(`${report.profileCount} deterministic device profiles compiled.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function showPolyglotIndex() {
  try {
    const report = await api("/api/product/polyglot"); text("productLabResult", proofJson(report)); toast(`${report.fileCount} product source files indexed.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function showProductArena() {
  try {
    const report = await api("/api/product/arena"); text("productLabResult", proofJson(report)); toast(`Product arena: ${report.status}.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function loadCreationForge() {
  try {
    const payload = await api("/api/creation"); const surfaces = payload.surfaces || {};
    text("creationCreativeState", surfaces.creativeCompass || "READY");
    text("creationAiState", surfaces.aiSystemArchitect || "READY");
    text("creationAutomationState", surfaces.automationCompiler || "READY");
    text("creationOpenState", surfaces.openToolFoundry || "READY");
  } catch (error) { toast(error.message, "bad"); }
}

function renderCreativeDirections(directions) {
  const root = $("creativeDirectionCards"); root.replaceChildren();
  directions.forEach((direction, index) => {
    const card = document.createElement("article"); const number = document.createElement("span"); number.textContent = String(index + 1).padStart(2, "0");
    const name = document.createElement("strong"); name.textContent = direction.name; const thesis = document.createElement("small"); thesis.textContent = direction.thesis;
    const grammar = document.createElement("ul"); (direction.visualGrammar || []).slice(0, 3).forEach((item) => { const row = document.createElement("li"); row.textContent = item; grammar.append(row); });
    card.append(number, name, thesis, grammar); root.append(card);
  });
}

async function compileCreativeBrief() {
  const payload = {
    name: $("creationName").value.trim(), outcome: $("creationOutcome").value.trim(),
    audiences: separatedValues($("creationAudiences").value, ","), moments: separatedValues($("creationMoments").value, "\n"),
    principles: separatedValues($("creationPrinciples").value, "\n"), avoid: separatedValues($("creationAvoid").value, "\n"),
    directionCount: Number($("creationDirectionCount").value),
  };
  $("compileCreativeBriefButton").disabled = true;
  try {
    const report = await api("/api/creation/creative-brief", { method: "POST", body: JSON.stringify(payload) });
    renderCreativeDirections(report.directions || []); text("creationCreativeState", `${report.directions.length} DIRECTIONS`); toast("Creative space illuminated without collapsing to an unevidenced winner.");
  } catch (error) { toast(error.message, "bad"); }
  finally { $("compileCreativeBriefButton").disabled = false; }
}

async function compileAiSystem() {
  const payload = {
    name: `${$("creationName").value.trim()} intelligence`, outcome: $("creationOutcome").value.trim(),
    userDecisions: separatedValues($("creationAiDecisions").value, "\n"), inputs: separatedValues($("creationAiInputs").value, ","),
    outputs: separatedValues($("creationAiOutputs").value, ","), tools: ["source search", "validation runner", "human approval"],
    risks: ["prompt injection", "false completion", "duplicate external effect", "uncalibrated confidence"],
  };
  try {
    const report = await api("/api/creation/ai-system", { method: "POST", body: JSON.stringify(payload) });
    text("creationAiResult", proofJson(report)); text("creationAiState", "REVIEW READY"); toast("Human-centered AI architecture compiled.");
  } catch (error) { toast(error.message, "bad"); }
}

function referenceAutomationPayload() {
  return {
    name: `${$("creationName").value.trim()} review flow`, outcome: "Draft, review, and apply one bounded effect without duplicate execution.",
    nodes: [
      { id: "start", kind: "trigger", label: "Request received", risk: "none", timeoutSeconds: 30 },
      { id: "draft", kind: "ai", label: "Draft bounded proposal", risk: "low", timeoutSeconds: 120, idempotencyKey: "creation:draft" },
      { id: "approve", kind: "human", label: "Review exact effect", risk: "none", timeoutSeconds: 86400 },
      { id: "apply", kind: "action", label: "Apply bounded effect", risk: "high", timeoutSeconds: 60, idempotencyKey: "creation:apply", compensation: "restore the content-addressed checkpoint" },
      { id: "done", kind: "terminal", label: "Record terminal state", risk: "none", timeoutSeconds: 30 },
    ],
    edges: [
      { from: "start", to: "draft" }, { from: "draft", to: "approve" },
      { from: "approve", to: "apply", condition: "approved" }, { from: "apply", to: "done" },
    ],
  };
}

async function compileReferenceAutomation() {
  try {
    const report = await api("/api/creation/automation", { method: "POST", body: JSON.stringify(referenceAutomationPayload()) });
    text("creationAutomationResult", proofJson(report)); text("creationAutomationState", report.status); toast(`Automation contract: ${report.status}.`, report.status === "READY_FOR_DRY_RUN" ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
}

async function compileOpenTool() {
  const objectSchema = { type: "object", properties: { traceId: { type: "string" } }, required: ["traceId"] };
  const payload = { name: "inspect_trace", description: "Read one content-addressed trace without changing runtime state.", permissions: ["artifact:read", "runtime:read"], risk: "read-only", approval: "never", idempotency: "content digest", timeoutSeconds: 30, inputSchema: objectSchema, outputSchema: { type: "object", properties: { status: { type: "string" } }, required: ["status"] } };
  try { const report = await api("/api/creation/tool", { method: "POST", body: JSON.stringify(payload) }); text("creationToolkitResult", proofJson(report)); toast("Portable Blunder and MCP tool contract compiled."); }
  catch (error) { toast(error.message, "bad"); }
}

async function compilePromptProgram() {
  const payload = { name: "Trace analyst", objective: "Explain the first failed decision using trace evidence.", instructions: ["Separate observations from inference", "Name missing evidence", "Preserve honest blocked states"], contextSources: ["content-addressed trace", "evaluation policy"], prohibited: ["invented tool results", "silent authority expansion"], tokenBudget: 4096, inputSchema: { type: "object", properties: { traceId: { type: "string" } } }, outputSchema: { type: "object", properties: { finding: { type: "string" } } } };
  try { const report = await api("/api/creation/prompt", { method: "POST", body: JSON.stringify(payload) }); text("creationToolkitResult", proofJson(report)); toast("Schema-bound prompt and context program compiled."); }
  catch (error) { toast(error.message, "bad"); }
}

async function gradeCreationTrace() {
  const payload = { traceId: "creation-reference", spans: [
    { id: "input", kind: "input", name: "Operator intent", status: "completed", durationMs: 1 },
    { id: "tool", parentId: "input", kind: "tool", name: "Evidence search", status: "completed", durationMs: 8, toolApproved: true },
    { id: "output", parentId: "input", kind: "output", name: "Grounded architecture", status: "completed", durationMs: 2, grounded: true },
  ] };
  try { const report = await api("/api/creation/trace", { method: "POST", body: JSON.stringify(payload) }); text("creationToolkitResult", proofJson(report)); toast(`Reference trace: ${report.status}.`, report.status === "PASS" ? "good" : "bad"); }
  catch (error) { toast(error.message, "bad"); }
}

async function openCreativeArena() {
  const payload = { candidates: [
    { id: "precision", novelty: .35, usefulness: .94, coherence: .95, accessibility: .96, evidence: "prototype-a" },
    { id: "signal", novelty: .88, usefulness: .91, coherence: .84, accessibility: .9, evidence: "prototype-b" },
    { id: "kinetic", novelty: .86, usefulness: .72, coherence: .55, accessibility: .82, evidence: "prototype-c" },
  ] };
  try { const report = await api("/api/creation/archive", { method: "POST", body: JSON.stringify(payload) }); text("creationToolkitResult", proofJson(report)); toast(`${report.archive.length} creative niches preserved for review.`); }
  catch (error) { toast(error.message, "bad"); }
}

async function loadKernel() {
  try {
    const payload = await api("/api/kernel"); const graph = payload.graph || {}; const counts = graph.counts || {}; const microspaces = payload.microspaces || {}; const quorum = payload.quorum || {}; const metabolism = payload.metabolism || {};
    text("kernelGraphState", payload.status);
    text("kernelGraphDetail", `${counts.nodes || 0} nodes / ${counts.edges || 0} edges`);
    text("kernelShardState", microspaces.arbitraryCommands === false ? "ALLOWLISTED" : "REVIEW");
    text("kernelQuorumState", quorum.requiredAgreement || "UNANIMOUS");
    text("kernelMetabolismState", metabolism.status || "TELEMETRY_REQUIRED");
    text("kernelQuorumResult", proofJson({ quorum, authority: payload.authority, proofPolicy: { formalMathematicalProof: false, autonomousThresholdPromotionRequired: true, operatorPromotionRequired: false } }));
  } catch (error) { toast(error.message, "bad"); }
}

async function refreshKernelGraph() {
  try {
    const payload = await api("/api/kernel/graph");
    text("kernelGraphResult", proofJson({ schema: payload.schema, status: payload.status, fingerprint: payload.fingerprint, repositoryFingerprint: payload.repositoryFingerprint, counts: payload.counts, changedPaths: payload.changedPaths, authority: payload.authority }));
    text("kernelGraphDetail", `${payload.counts.nodes} nodes / ${payload.counts.edges} edges`); toast("Semantic graph refreshed from exact source identity.");
  } catch (error) { toast(error.message, "bad"); }
}

async function analyzeKernelImpact() {
  const changedPaths = separatedValues($("kernelImpactPaths").value, ","); const maximumDepth = Number($("kernelImpactDepth").value);
  try {
    const payload = await api("/api/kernel/impact", { method: "POST", body: JSON.stringify({ changedPaths, maximumDepth }) });
    text("kernelGraphResult", proofJson(payload)); toast(`${payload.counts.nodes} semantic nodes in the bounded impact frontier.`);
  } catch (error) { toast(error.message, "bad"); }
}

async function runKernelProof() {
  const body = { maximumParallel: 2, specs: [
    { id: "kernel-python-contracts", commandKind: "python-compile", targets: ["blunder/quorum_graph.py", "blunder/deterministic_shards.py", "blunder/quorum_kernel.py"], repeatCount: 2, timeoutSeconds: 30, memoryBytes: 536870912 },
    { id: "kernel-shell-syntax", commandKind: "node-check", targets: ["blunder/shell/static/app.js"], repeatCount: 2, timeoutSeconds: 30, memoryBytes: 536870912 },
  ] };
  $("runKernelProofButton").disabled = true;
  try {
    const payload = await api("/api/kernel/shards", { method: "POST", body: JSON.stringify(body) });
    text("kernelShardResult", proofJson(payload)); text("kernelShardState", payload.status); toast(`Kernel micro-space proof: ${payload.status}.`, payload.status === "PASS" ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
  finally { $("runKernelProofButton").disabled = false; }
}

async function compileMetabolicPlan() {
  const activeForeground = Boolean(state.activeJob && ["queued", "running", "cancelling"].includes(state.activeJob.status));
  const body = {
    telemetry: {
      measurementSource: "operator-entered-kernel-ui",
      operatorIdle: $("kernelOperatorIdle").checked,
      foregroundMissionActive: activeForeground,
      cpuPercent: Number($("kernelCpuPercent").value),
      availableMemoryBytes: Math.round(Number($("kernelMemoryGiB").value) * 1024 * 1024 * 1024),
      thermalState: $("kernelThermalState").value,
      queueFailures: Number((((state.workspace || {}).queue || {}).counts || {}).failed || 0),
      destructiveAuthority: false,
    },
    budget: { maximumTasks: 3, maximumSeconds: 180, maximumCpuPercent: 35, minimumAvailableMemoryBytes: 2147483648 },
  };
  try {
    const payload = await api("/api/kernel/metabolism", { method: "POST", body: JSON.stringify(body) });
    text("kernelMetabolicResult", proofJson(payload)); text("kernelMetabolismState", payload.status); toast(`Metabolic planner: ${payload.status}.`, payload.status.startsWith("READY") ? "good" : "bad");
  } catch (error) { toast(error.message, "bad"); }
}

function renderSessions(sessions) {
  const root = $("missionSessions"); root.replaceChildren();
  if (!sessions.length) { const p = document.createElement("p"); p.textContent = "No active sessions."; root.append(p); return; }
  sessions.forEach((session) => {
    const card = document.createElement("article"); card.className = "session-card";
    const pulse = document.createElement("span"); pulse.className = `session-pulse ${session.status}`;
    const copy = document.createElement("div"); const title = document.createElement("strong"); title.textContent = session.label || session.id;
    const detail = document.createElement("small"); detail.textContent = session.pidValid === true ? "PID identity verified" : display(session.status, "unknown");
    copy.append(title, detail); card.append(pulse, copy); root.append(card);
  });
}

function renderMissionHistory(rows) {
  const root = $("missionHistory"); root.replaceChildren();
  if (!rows.length) { const p = document.createElement("p"); p.className = "empty-copy"; p.textContent = "Completed actions will persist here across shell restarts."; root.append(p); return; }
  rows.forEach((mission) => {
    const row = document.createElement("article"); row.className = "mission-row";
    const head = document.createElement("div"); const title = document.createElement("strong"); title.textContent = mission.label || mission.action;
    const status = document.createElement("span"); status.className = `status-chip ${mission.status === "completed" ? "good" : mission.status === "failed" ? "bad" : ""}`; status.textContent = mission.status;
    head.append(title, status); const meta = document.createElement("small"); meta.textContent = mission.finishedAt ? new Date(mission.finishedAt).toLocaleString() : "In progress";
    row.append(head, meta);
    if (mission.intent) {
      const reopen = document.createElement("button"); reopen.type = "button"; reopen.className = "reopen-button"; reopen.textContent = "Reopen with fresh evidence";
      reopen.addEventListener("click", () => { $("taskInput").value = mission.intent; setView("work"); buildBriefing(); }); row.append(reopen);
    }
    root.append(row);
  });
}

function renderCheckpoints(rows) {
  const root = $("checkpointList"); root.replaceChildren();
  if (!rows.length) { const p = document.createElement("p"); p.className = "empty-copy"; p.textContent = "New native changes will create reviewable checkpoints."; root.append(p); return; }
  rows.forEach((checkpoint) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "checkpoint-row";
    const head = document.createElement("span"); const title = document.createElement("strong"); title.textContent = checkpoint.summary || checkpoint.task || checkpoint.id;
    const stateLabel = document.createElement("i"); stateLabel.textContent = checkpoint.current ? "current" : "drifted"; head.append(title, stateLabel);
    const meta = document.createElement("small"); meta.textContent = `${checkpoint.files.length} files · ${checkpoint.lens || "bounded"} lens`;
    button.append(head, meta); button.addEventListener("click", () => openCheckpoint(checkpoint.id)); root.append(button);
  });
}

function missionStatusTone(status) {
  if (status === "completed") return "good";
  if (["failed", "cancelled", "interrupted"].includes(status)) return "bad";
  return "";
}

function renderMissionBranches(payload) {
  const capacity = payload.capacity || {};
  text("missionBranchCapacity", payload.available ? `${capacity.active || 0} / ${capacity.maximum || 0} active` : "Configuration required");
  $("spawnMissionBranchButton").disabled = !payload.available || Number(capacity.available || 0) < 1;
  const root = $("missionBranchList"); root.replaceChildren();
  const rows = payload.missions || []; state.missionBranches = rows;
  if (!payload.available) {
    const p = document.createElement("p"); p.className = "empty-copy"; p.textContent = payload.unavailableReason || "Mission branches are unavailable."; root.append(p); return;
  }
  if (!rows.length) {
    const p = document.createElement("p"); p.className = "empty-copy"; p.textContent = "No branch agents yet. Spawn an independently reviewable coding mission above."; root.append(p); return;
  }
  rows.forEach((mission) => {
    const card = document.createElement("article"); card.className = `branch-card ${mission.status}`;
    const head = document.createElement("div"); head.className = "branch-card-head";
    const id = document.createElement("code"); id.textContent = mission.id;
    const status = document.createElement("span"); status.className = `status-chip ${missionStatusTone(mission.status)}`; status.textContent = mission.status;
    head.append(id, status);
    const title = document.createElement("h3"); title.textContent = mission.task;
    const branch = document.createElement("code"); branch.textContent = mission.branch;
    const skills = document.createElement("div"); skills.className = "branch-skill-strip";
    ((mission.capabilities || {}).skills || []).forEach((skill) => { const chip = document.createElement("span"); chip.textContent = skill.name || skill.id; skills.append(chip); });
    const detail = document.createElement("small");
    const changed = (mission.changedFiles || []).length;
    const usage = mission.budgetUsage || {}; const budgetCopy = usage.providerCalls === undefined ? "" : ` · ${usage.providerCalls} calls · ${usage.totalTokens || 0} tokens · $${Number(usage.costUsd || 0).toFixed(4)}`;
    detail.textContent = mission.status === "running" ? `PID ${mission.pid} · isolated process and context` : `${changed} mission-only files · ${mission.integrationState || "PENDING"}${budgetCopy}`;
    const actions = document.createElement("div"); actions.className = "branch-card-actions";
    const inspect = document.createElement("button"); inspect.type = "button"; inspect.textContent = mission.patchPreview ? "Review patch" : "Inspect"; inspect.addEventListener("click", () => openMissionBranch(mission.id)); actions.append(inspect);
    if (["preparing", "running", "cancelling"].includes(mission.status)) {
      const stop = document.createElement("button"); stop.type = "button"; stop.className = "stop-branch"; stop.textContent = "Stop"; stop.addEventListener("click", () => cancelMissionBranch(mission.id)); actions.append(stop);
    }
    card.append(head, title, branch, skills, detail, actions); root.append(card);
  });
}

function renderMissionPrograms(payload) {
  const rows = payload.programs || []; state.missionPrograms = rows;
  const root = $("missionProgramList"); root.replaceChildren();
  if (!rows.length) { const p = document.createElement("p"); p.className = "empty-copy"; p.textContent = "No mission programs yet."; root.append(p); return; }
  rows.forEach((program) => {
    const card = document.createElement("article"); card.className = "program-card";
    const head = document.createElement("div"); const title = document.createElement("strong"); title.textContent = program.task;
    const status = document.createElement("span"); status.className = `status-chip ${missionStatusTone(program.status)}`; status.textContent = program.status; head.append(title, status);
    const plan = program.plan || program; const nodes = plan.nodes || []; const specialists = nodes.filter((node) => node.role === "specialist");
    const detail = document.createElement("small"); detail.textContent = `${specialists.length} parallel specialists · ${plan.edgeCount || specialists.length} dependency edges · synthesis gate`;
    card.append(head, detail);
    if (program.status === "ready_for_synthesis") {
      const button = document.createElement("button"); button.className = "primary-button"; button.type = "button"; button.textContent = "Synthesize reviewed patches"; button.addEventListener("click", () => synthesizeMissionProgram(program.id)); card.append(button);
    }
    root.append(card);
  });
}

async function previewMissionProgram() {
  const task = $("missionProgramTask").value.trim();
  try { const plan = await api("/api/mission-programs/preview", { method: "POST", body: JSON.stringify({ task }) }); renderMissionPrograms({ programs: [{ ...plan, id: "preview", status: "preview", plan }] }); toast(`${plan.parallelWidth} non-overlapping specialists planned with ${plan.edgeCount} synthesis dependencies.`); }
  catch (error) { toast(error.message, "bad"); }
}

async function launchMissionProgram() {
  const task = $("missionProgramTask").value.trim();
  try { const program = await api("/api/mission-programs", { method: "POST", body: JSON.stringify({ task, providerApproved: $("missionProgramApproval").checked }) }); toast(`${program.id} launched through the isolated mission fabric.`); $("missionProgramTask").value = ""; await loadMissions(); }
  catch (error) { toast(error.message, "bad"); }
}

async function synthesizeMissionProgram(programId) {
  try { await api(`/api/mission-programs/${encodeURIComponent(programId)}/synthesize`, { method: "POST", body: "{}" }); toast(`${programId} synthesized and validated in its own branch.`); await loadMissions(); }
  catch (error) { toast(error.message, "bad"); }
}

async function loadMissionBranches() {
  if (state.missionBranchTimer) clearTimeout(state.missionBranchTimer);
  const payload = await api("/api/mission-branches"); renderMissionBranches(payload);
  const active = (payload.missions || []).some((mission) => ["preparing", "running", "cancelling"].includes(mission.status));
  if (active && state.view === "missions") state.missionBranchTimer = setTimeout(() => loadMissionBranches().catch((error) => toast(error.message, "bad")), 1500);
}

async function spawnMissionBranch() {
  const task = $("missionBranchTask").value.trim(); $("spawnMissionBranchButton").disabled = true;
  try {
    const mission = await api("/api/mission-branches", { method: "POST", body: JSON.stringify({ task, providerApproved: $("missionBranchApproval").checked }) });
    $("missionBranchTask").value = ""; toast(`${mission.id} is running in an isolated worktree.`); await loadMissionBranches();
  } catch (error) { toast(error.message, "bad"); await loadMissionBranches().catch(() => {}); }
}

async function cancelMissionBranch(missionId) {
  try { await api(`/api/mission-branches/${encodeURIComponent(missionId)}/cancel`, { method: "POST", body: "{}" }); toast(`${missionId} stopped.`); await loadMissionBranches(); }
  catch (error) { toast(error.message, "bad"); }
}

async function openMissionBranch(missionId) {
  try {
    const mission = await api(`/api/mission-branches/${encodeURIComponent(missionId)}`);
    state.selectedMissionBranch = missionId; text("missionBranchDetailTitle", mission.task || mission.id); text("missionBranchIntegrationState", mission.integrationState || mission.status);
    $("missionBranchIntegrationState").className = `status-chip ${mission.integrationState === "READY_TO_APPLY" ? "good" : missionStatusTone(mission.status)}`;
    text("missionBranchPatch", mission.patchPreview || mission.error || "The mission has not produced a patch yet.");
    const checks = mission.primaryCompatibility || []; const compatible = checks.filter((item) => item.compatible).length;
    text("missionBranchCompatibility", checks.length ? `${compatible} / ${checks.length} changed paths match the seeded primary state. Patch: ${mission.patch || "pending"}` : "Integration remains review-only until the mission produces a drift-checked patch.");
    $("missionBranchIntegrationConfirmation").value = ""; $("missionBranchIntegrationConfirmation").placeholder = `INTEGRATE ${missionId}`;
    $("integrateMissionBranchButton").disabled = mission.integrationState !== "READY_TO_APPLY";
    $("reconcileMissionBranchButton").disabled = mission.integrationState !== "PRIMARY_DRIFTED";
    $("cleanupMissionBranchButton").disabled = ["preparing", "running", "cancelling"].includes(mission.status) || mission.worktreeRemoved === true;
    $("recordMissionOutcomeButton").disabled = !["completed", "failed", "cancelled", "interrupted"].includes(mission.status);
    $("missionBranchDetail").classList.remove("hidden"); $("missionBranchDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message, "bad"); }
}

async function reconcileMissionBranch() {
  if (!state.selectedMissionBranch) return;
  try { const mission = await api(`/api/mission-branches/${encodeURIComponent(state.selectedMissionBranch)}/reconcile`, { method: "POST", body: JSON.stringify({ confirmation: $("missionBranchIntegrationConfirmation").value }) }); toast(`${mission.id} reconciled against current primary evidence.`); await loadMissionBranches(); await openMissionBranch(mission.id); }
  catch (error) { $("missionBranchIntegrationConfirmation").placeholder = `RECONCILE ${state.selectedMissionBranch}`; toast(error.message, "bad"); }
}

async function cleanupMissionBranch() {
  if (!state.selectedMissionBranch) return;
  try { const mission = await api(`/api/mission-branches/${encodeURIComponent(state.selectedMissionBranch)}/cleanup`, { method: "POST", body: JSON.stringify({ confirmation: $("missionBranchIntegrationConfirmation").value }) }); toast(`${mission.id} worktrees and branches cleaned; evidence retained.`); await loadMissionBranches(); await openMissionBranch(mission.id); }
  catch (error) { $("missionBranchIntegrationConfirmation").placeholder = `CLEAN ${state.selectedMissionBranch}`; toast(error.message, "bad"); }
}

async function recordMissionOutcome() {
  if (!state.selectedMissionBranch) return;
  try { const outcome = await api(`/api/mission-branches/${encodeURIComponent(state.selectedMissionBranch)}/outcome`, { method: "POST", body: JSON.stringify({ status: $("missionOutcomeStatus").value, score: Number($("missionOutcomeScore").value), metrics: {}, note: "Recorded through Mission Control" }) }); toast(`Outcome recorded: ${outcome.status} · ${outcome.score}/5.`); await loadMissions(); }
  catch (error) { toast(error.message, "bad"); }
}

async function integrateMissionBranch() {
  if (!state.selectedMissionBranch) return;
  try {
    const mission = await api(`/api/mission-branches/${encodeURIComponent(state.selectedMissionBranch)}/integrate`, { method: "POST", body: JSON.stringify({ confirmation: $("missionBranchIntegrationConfirmation").value }) });
    toast(`${mission.id} was applied to the uncommitted review tree.`); await loadMissionBranches(); await openMissionBranch(mission.id); await loadWorkspace(true);
  } catch (error) { toast(error.message, "bad"); }
}

async function loadMissions() {
  try {
    const [payload, branches, programs, intelligence, learning] = await Promise.all([api("/api/missions"), api("/api/mission-branches"), api("/api/mission-programs"), api("/api/repository-intelligence"), api("/api/mission-learning")]);
    renderSessions(payload.sessions || []); renderMissionHistory(payload.history || []); renderCheckpoints(payload.checkpoints || []); renderMissionBranches(branches); renderMissionPrograms(programs);
    const counts = intelligence.counts || {}; text("intelligenceFileCount", counts.files || 0); text("intelligenceSymbolCount", `${counts.symbols || 0} symbols · ${counts.tests || 0} tests`);
    text("learningOutcomeCount", learning.outcomes || 0); text("learningAverageScore", learning.averageScore === null ? "awaiting scored outcomes" : `${learning.averageScore}/5 average`); text("fabricSecurityState", "Sandbox enforced");
    const active = (branches.missions || []).some((mission) => ["preparing", "running", "cancelling"].includes(mission.status));
    if (active) state.missionBranchTimer = setTimeout(() => loadMissionBranches().catch((error) => toast(error.message, "bad")), 1500);
  } catch (error) { toast(error.message, "bad"); }
}

async function openCheckpoint(checkpointId) {
  try {
    const payload = await api(`/api/checkpoints/${encodeURIComponent(checkpointId)}`); const checkpoint = payload.checkpoint || {};
    state.selectedCheckpoint = checkpointId; text("checkpointTitle", checkpoint.summary || checkpoint.task || checkpointId);
    text("checkpointState", checkpoint.current ? "Current · restorable" : "Drifted · review only");
    $("checkpointState").className = `status-chip ${checkpoint.current ? "good" : ""}`;
    text("checkpointDiff", (payload.diffs || []).map((item) => item.diff || `No visible text change for ${item.path}`).join("\n"));
    $("restoreConfirmation").value = ""; $("restoreConfirmation").placeholder = payload.restoreConfirmation;
    $("restoreCheckpointButton").disabled = !checkpoint.current || !checkpoint.restorable; $("checkpointDetail").classList.remove("hidden");
  } catch (error) { toast(error.message, "bad"); }
}

async function restoreSelectedCheckpoint() {
  if (!state.selectedCheckpoint) return;
  try {
    const payload = await api(`/api/checkpoints/${encodeURIComponent(state.selectedCheckpoint)}/restore`, { method: "POST", body: JSON.stringify({ confirmation: $("restoreConfirmation").value }) });
    toast(`${payload.files.length} files restored from the guarded checkpoint.`); state.selectedCheckpoint = null; $("checkpointDetail").classList.add("hidden"); await loadMissions(); await loadWorkspace(true);
  } catch (error) { toast(error.message, "bad"); }
}

async function searchFiles(query) {
  const root = $("fileResults");
  if (query.trim().length < 2) { root.innerHTML = "<p>Type two characters to search first-party source.</p>"; return; }
  try {
    const endpoint = state.searchMode === "content" ? "/api/search" : "/api/files";
    const payload = await api(`${endpoint}?q=${encodeURIComponent(query)}`); root.replaceChildren();
    if (!payload.results.length) { const p = document.createElement("p"); p.textContent = "No first-party source matched."; root.append(p); return; }
    payload.results.forEach((result) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "file-result"; button.dataset.path = result.path;
      const strong = document.createElement("strong"); strong.textContent = result.name || `${result.path}:${result.line}`;
      const small = document.createElement("small"); small.textContent = result.preview || result.path;
      button.append(strong, small); button.addEventListener("click", () => openFile(result.path, button, result.line)); root.append(button);
    });
  } catch (error) { toast(error.message, "bad"); }
}

async function openFile(path, button, line = null) {
  try {
    const payload = await api(`/api/file?path=${encodeURIComponent(path)}`);
    state.editorPath = payload.path; state.editorSha256 = payload.sha256; state.editorSaveConfirmation = payload.saveConfirmation;
    text("filePath", line ? `${payload.path} · line ${line}` : payload.path); text("fileBytes", `${payload.bytes.toLocaleString()} bytes`); text("editorState", "Clean");
    $("fileContent").disabled = false; $("fileContent").value = payload.content; $("editorSaveConfirmation").disabled = false; $("editorSaveConfirmation").value = ""; $("editorSaveConfirmation").placeholder = payload.saveConfirmation; $("editorSaveButton").disabled = false;
    renderEditorAnalysis(payload.analysis || {});
    all(".file-result").forEach((node) => node.classList.remove("active")); if (button.classList.contains("file-result")) button.classList.add("active");
    if (line) goToEditorLine(line);
  } catch (error) { toast(error.message, "bad"); }
}

function goToEditorLine(line, column = 1) {
  const editor = $("fileContent"); const lines = editor.value.split("\n"); const bounded = Math.max(1, Math.min(Number(line) || 1, lines.length));
  const offset = lines.slice(0, bounded - 1).reduce((total, value) => total + value.length + 1, 0) + Math.max(0, Number(column) - 1);
  editor.focus(); editor.setSelectionRange(offset, offset); editor.scrollTop = Math.max(0, (bounded - 5) * 17.4); updateCursorPosition();
}

function editorButton(label, className, handler) {
  const button = document.createElement("button"); button.type = "button"; button.textContent = label; if (className) button.className = className; button.addEventListener("click", handler); return button;
}

function renderEditorAnalysis(analysis) {
  const symbols = analysis.symbols || []; const diagnostics = analysis.diagnostics || [];
  text("symbolCount", symbols.length); text("diagnosticCount", diagnostics.length);
  const outline = $("editorOutline"); outline.replaceChildren();
  if (!symbols.length) { const p = document.createElement("p"); p.textContent = "No navigable symbols."; outline.append(p); }
  symbols.forEach((item) => outline.append(editorButton(`${item.kind} ${item.name} · ${item.line}`, "", () => goToEditorLine(item.line, item.column))));
  const diagnosticRoot = $("editorDiagnostics"); diagnosticRoot.replaceChildren();
  if (!diagnostics.length) { const p = document.createElement("p"); p.textContent = "No diagnostics."; diagnosticRoot.append(p); }
  diagnostics.forEach((item) => diagnosticRoot.append(editorButton(`${item.line}:${item.column} ${item.message}`, item.severity, () => goToEditorLine(item.line, item.column))));
}

async function analyzeEditor() {
  if (!state.editorPath) return;
  try { renderEditorAnalysis(await api("/api/editor/analyze", { method: "POST", body: JSON.stringify({ path: state.editorPath, content: $("fileContent").value }) })); }
  catch (error) { toast(error.message, "bad"); }
}

function updateCursorPosition() {
  const editor = $("fileContent"); const before = editor.value.slice(0, editor.selectionStart); const rows = before.split("\n"); text("cursorPosition", `Ln ${rows.length}, Col ${rows[rows.length - 1].length + 1}`);
}

async function findDefinition() {
  const symbol = $("definitionSymbol").value.trim();
  try {
    const payload = await api(`/api/editor/definitions?symbol=${encodeURIComponent(symbol)}`); const rows = payload.locations || []; text("definitionCount", rows.length); const root = $("definitionResults"); root.replaceChildren();
    if (!rows.length) { const p = document.createElement("p"); p.textContent = "No definition found."; root.append(p); return; }
    rows.forEach((item) => root.append(editorButton(`${item.path}:${item.line} · ${item.kind}`, "", async () => { await openFile(item.path, document.createElement("button"), item.line); })));
  } catch (error) { toast(error.message, "bad"); }
}

async function saveEditor() {
  if (!state.editorPath) return;
  try {
    const payload = await api("/api/editor/save", { method: "POST", body: JSON.stringify({ path: state.editorPath, content: $("fileContent").value, expectedSha256: state.editorSha256, confirmation: $("editorSaveConfirmation").value }) });
    state.editorSha256 = payload.afterSha256; state.editorSaveConfirmation = `SAVE ${state.editorPath} ${payload.afterSha256.slice(0, 12)}`; $("editorSaveConfirmation").value = ""; $("editorSaveConfirmation").placeholder = state.editorSaveConfirmation; text("editorState", "Saved · review tree"); renderEditorAnalysis(payload.analysis || {}); toast("Source saved behind a restorable checkpoint. No commit or push occurred.");
  } catch (error) { toast(error.message, "bad"); }
}

const commands = [
  { name: "Go to Work", description: "Open the focused task composer", key: "W", run: () => setView("work") },
  { name: "Go to Evolve", description: "Inspect adaptive mutation pressure and elites", key: "E", run: () => setView("evolve") },
  { name: "Go to Capabilities", description: "Route tasks through native skills, tools, plugins, and hooks", key: "C", run: () => setView("capabilities") },
  { name: "Go to Missions", description: "Inspect live sessions, durable history, and checkpoints", key: "M", run: () => setView("missions") },
  { name: "Go to Frontier", description: "Use discovery, language intelligence, and bounded self-coding preflight", key: "N", run: () => setView("frontier") },
  { name: "Go to Product Studio", description: "Build mobile, web, UI/UX, game, asset, device, and product proof", key: "S", run: () => setView("product") },
  { name: "Go to Creation Forge", description: "Design creative directions, AI systems, automations, tools, prompts, traces, and arenas", key: "A", run: () => setView("creation") },
  { name: "Go to Quorum Kernel", description: "Inspect semantic topology, isolated proof, consensus, and metabolism", key: "Q", run: () => setView("kernel") },
  { name: "Go to Files", description: "Search first-party Blunder source", key: "F", run: () => setView("files") },
  { name: "Build task briefing", description: "Map evidence and gates without spending a provider call", key: "B", run: buildBriefing },
  { name: "Run planning gate", description: "No provider, no benchmark, no queue mutation", key: "P", run: () => startAction("planning_gate") },
  { name: "Run safety tests", description: "Execute the integration baseline", key: "T", run: () => startAction("tests") },
  { name: "Refresh workspace", description: "Reload status, queue, and evolution state", key: "R", run: () => startAction("status") },
];

function renderPalette(filter = "") {
  const root = $("paletteList"); root.replaceChildren();
  const visible = commands.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(filter.toLowerCase()));
  visible.forEach((item, index) => {
    const button = document.createElement("button"); button.type = "button"; button.className = `palette-item${index === 0 ? " selected" : ""}`;
    const copy = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = item.name; const small = document.createElement("small"); small.textContent = item.description; copy.append(strong, small);
    const key = document.createElement("span"); key.textContent = item.key; button.append(copy, key);
    button.addEventListener("click", () => { closePalette(); item.run(); }); root.append(button);
  });
}

function openPalette() { $("palette").classList.remove("hidden"); $("paletteInput").value = ""; renderPalette(); setTimeout(() => $("paletteInput").focus(), 0); }
function closePalette() { $("palette").classList.add("hidden"); }

function bindEvents() {
  all("[data-view-target]").forEach((node) => node.addEventListener("click", () => setView(node.dataset.viewTarget)));
  all("[data-action]").forEach((node) => node.addEventListener("click", () => startAction(node.dataset.action)));
  $("refreshButton").addEventListener("click", () => loadWorkspace(true));
  $("runTaskButton").addEventListener("click", runTask);
  $("briefTaskButton").addEventListener("click", buildBriefing);
  $("routeCapabilitiesButton").addEventListener("click", routeCapabilities);
  $("runDiscoveryButton").addEventListener("click", runDiscovery);
  $("findReferencesButton").addEventListener("click", findReferences);
  $("findCompletionsButton").addEventListener("click", findCompletions);
  $("workspaceDiagnosticsButton").addEventListener("click", loadWorkspaceDiagnostics);
  $("selfCodingPreflightButton").addEventListener("click", preflightSelfCoding);
  $("compileBlueprintButton").addEventListener("click", compileProductBlueprint);
  $("compileDesignSystemButton").addEventListener("click", compileProductDesignSystem);
  all(".product-audit-button").forEach((node) => node.addEventListener("click", () => auditProductProject(node.dataset.productDomain)));
  $("buildDeviceMatrixButton").addEventListener("click", showDeviceMatrix);
  $("runPolyglotButton").addEventListener("click", showPolyglotIndex);
  $("openProductArenaButton").addEventListener("click", showProductArena);
  $("compileCreativeBriefButton").addEventListener("click", compileCreativeBrief);
  $("compileAiSystemButton").addEventListener("click", compileAiSystem);
  $("compileAutomationButton").addEventListener("click", compileReferenceAutomation);
  $("compileOpenToolButton").addEventListener("click", compileOpenTool);
  $("compilePromptProgramButton").addEventListener("click", compilePromptProgram);
  $("gradeTraceButton").addEventListener("click", gradeCreationTrace);
  $("openCreativeArenaButton").addEventListener("click", openCreativeArena);
  $("refreshKernelGraphButton").addEventListener("click", refreshKernelGraph);
  $("analyzeKernelImpactButton").addEventListener("click", analyzeKernelImpact);
  $("runKernelProofButton").addEventListener("click", runKernelProof);
  $("compileMetabolicPlanButton").addEventListener("click", compileMetabolicPlan);
  $("refreshCcosButton").addEventListener("click", loadCCOS);
  $("appendCcosRecordButton").addEventListener("click", appendCCOSRecord);
  $("runCcosCanaryButton").addEventListener("click", runCCOSCanary);
  $("recoverCcosButton").addEventListener("click", recoverCCOS);
  $("stopJobButton").addEventListener("click", cancelActiveJob);
  $("commandButton").addEventListener("click", openPalette);
  $("paletteBackdrop").addEventListener("click", closePalette);
  $("refreshMissionsButton").addEventListener("click", loadMissions);
  $("spawnMissionBranchButton").addEventListener("click", spawnMissionBranch);
  $("integrateMissionBranchButton").addEventListener("click", integrateMissionBranch);
  $("reconcileMissionBranchButton").addEventListener("click", reconcileMissionBranch);
  $("cleanupMissionBranchButton").addEventListener("click", cleanupMissionBranch);
  $("recordMissionOutcomeButton").addEventListener("click", recordMissionOutcome);
  $("previewMissionProgramButton").addEventListener("click", previewMissionProgram);
  $("launchMissionProgramButton").addEventListener("click", launchMissionProgram);
  $("restoreCheckpointButton").addEventListener("click", restoreSelectedCheckpoint);
  $("searchModeButton").addEventListener("click", () => { state.searchMode = state.searchMode === "names" ? "content" : "names"; text("searchModeButton", state.searchMode === "content" ? "Content" : "Names"); searchFiles($("fileSearch").value); });
  $("definitionButton").addEventListener("click", findDefinition);
  $("definitionSymbol").addEventListener("keydown", (event) => { if (event.key === "Enter") findDefinition(); });
  $("editorSaveButton").addEventListener("click", saveEditor);
  $("fileContent").addEventListener("input", () => { text("editorState", "Modified"); clearTimeout(state.editorAnalysisTimer); state.editorAnalysisTimer = setTimeout(analyzeEditor, 260); updateCursorPosition(); });
  $("fileContent").addEventListener("click", updateCursorPosition);
  $("fileContent").addEventListener("keyup", updateCursorPosition);
  $("fileContent").addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveEditor(); } });
  $("paletteInput").addEventListener("input", (event) => renderPalette(event.target.value));
  $("paletteInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { const selected = $("paletteList").querySelector(".palette-item"); if (selected) selected.click(); }
  });
  $("fileSearch").addEventListener("input", (event) => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => searchFiles(event.target.value), 220); });
  $("taskInput").addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); runTask(); } });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openPalette(); }
    if (event.key === "Escape") closePalette();
  });
  window.addEventListener("hashchange", () => { const target = location.hash.slice(1); if (["evolve", "capabilities", "missions", "frontier", "product", "creation", "kernel", "files"].includes(target)) setView(target); });
}

async function start() {
  bootstrapToken(); bindEvents();
  const hashView = location.hash.slice(1); if (["evolve", "capabilities", "missions", "frontier", "product", "creation", "kernel", "files"].includes(hashView)) setView(hashView);
  await loadWorkspace(false); await pollJobs();
}

start();
