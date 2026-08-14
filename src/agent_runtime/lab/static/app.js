const state = {
  scenarios: [],
  selectedScenario: null,
  runId: null,
  snapshot: null,
  selectedEventIndex: -1,
  revealedEventIndex: -1,
  activeTab: "event",
  eventSource: null,
  autoplay: null,
  followLive: true,
};

const $ = (id) => document.getElementById(id);
const terminalStatuses = new Set(["completed", "failed", "cancelled"]);
const sseEventTypes = [
  "run.created", "run.started", "run.resumed", "run.recovered", "run.paused",
  "run.completed", "run.failed", "run.cancelled", "model.requested",
  "model.stream.started", "model.delta", "model.stream.completed", "model.stream.failed",
  "model.completed", "tool.requested", "tool.started", "tool.completed", "tool.failed",
  "tool.rejected", "tool.cancelled", "tool.outcome_unknown", "tool.unknown_resolved",
  "approval.requested", "approval.resolved", "checkpoint.created", "step.completed",
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function json(value) {
  return escapeHtml(JSON.stringify(value, null, 2));
}

function eventCategory(type) {
  if (type.startsWith("run.")) return "run";
  if (type.startsWith("model.")) return "model";
  if (type.startsWith("tool.")) return type.includes("failed") ? "error" : "tool";
  if (type.startsWith("approval.")) return "approval";
  if (type.startsWith("checkpoint.") || type.startsWith("step.")) return "checkpoint";
  return "run";
}

function setConnection(mode, text) {
  const badge = $("connectionBadge");
  badge.className = `connection-badge ${mode}`;
  badge.innerHTML = `<span></span>${escapeHtml(text)}`;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 2200);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* noop */ }
    throw new Error(detail);
  }
  return response.json();
}

async function loadScenarios() {
  state.scenarios = await request("/lab/api/scenarios");
  $("scenarioCount").textContent = state.scenarios.length;
  renderScenarioList();
  selectScenario(state.scenarios[0]?.id);
}

function renderScenarioList() {
  $("scenarioList").innerHTML = state.scenarios.map((scenario, index) => `
    <button type="button" class="scenario-card ${scenario.id === state.selectedScenario?.id ? "active" : ""}" data-scenario="${escapeHtml(scenario.id)}">
      <span class="scenario-card-top">
        <span class="scenario-number">${String(index + 1).padStart(2, "0")}</span>
        <strong>${escapeHtml(scenario.name)}</strong>
      </span>
      <small>${escapeHtml(scenario.description)}</small>
    </button>
  `).join("");
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.addEventListener("click", () => selectScenario(button.dataset.scenario));
  });
}

function selectScenario(id) {
  const scenario = state.scenarios.find((item) => item.id === id);
  if (!scenario) return;
  state.selectedScenario = scenario;
  renderScenarioList();
  $("scenarioCategory").textContent = scenario.category;
  $("scenarioTags").textContent = scenario.tags.join(" · ");
  $("scenarioName").textContent = scenario.name;
  $("scenarioDescription").textContent = scenario.description;
  $("scenarioInput").value = scenario.input;
  $("learningPoints").innerHTML = scenario.learning_points
    .map((point) => `<span class="learning-point">${escapeHtml(point)}</span>`)
    .join("");
}

async function startScenario() {
  if (!state.selectedScenario) return;
  stopAutoplay();
  closeEventSource();
  $("startButton").disabled = true;
  $("startButton").textContent = "启动中…";
  setConnection("live", "创建 Run");
  try {
    const run = await request(`/lab/api/scenarios/${state.selectedScenario.id}/runs`, {
      method: "POST",
      body: JSON.stringify({ input: $("scenarioInput").value.trim() || null }),
    });
    state.runId = run.id;
    state.snapshot = null;
    state.selectedEventIndex = -1;
    state.revealedEventIndex = -1;
    state.followLive = true;
    $("runIdentity").textContent = run.id;
    connectEventSource(run.id);
    await refreshSnapshot();
  } catch (error) {
    setConnection("error", "启动失败");
    toast(error.message);
  } finally {
    $("startButton").disabled = false;
    $("startButton").textContent = "开始新 Run";
  }
}

function connectEventSource(runId) {
  closeEventSource();
  const source = new EventSource(`/runs/${runId}/events/stream?after_sequence=0`);
  state.eventSource = source;
  setConnection("live", "SSE 已连接");
  const handleEvent = async () => {
    try { await refreshSnapshot(); } catch (error) { toast(error.message); }
  };
  sseEventTypes.forEach((type) => source.addEventListener(type, handleEvent));
  source.onopen = () => setConnection("live", "SSE 实时接收");
  source.onerror = () => {
    if (state.snapshot && terminalStatuses.has(state.snapshot.run.status)) {
      setConnection("done", "事件已持久化");
      closeEventSource();
    }
  };
}

function closeEventSource() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
}

async function refreshSnapshot() {
  if (!state.runId) return;
  const snapshot = await request(`/lab/api/runs/${state.runId}/snapshot`);
  const oldLength = state.snapshot?.events.length || 0;
  state.snapshot = snapshot;
  const lastIndex = snapshot.events.length - 1;
  if (state.followLive || state.selectedEventIndex < 0) {
    state.revealedEventIndex = lastIndex;
    state.selectedEventIndex = lastIndex;
  } else if (snapshot.events.length > oldLength && state.revealedEventIndex >= oldLength - 1) {
    state.revealedEventIndex = lastIndex;
  }
  renderAll();
  if (terminalStatuses.has(snapshot.run.status)) {
    setConnection("done", "Run 已完成");
    closeEventSource();
  } else if (snapshot.run.status === "waiting_for_approval") {
    setConnection("live", "等待人工审批");
  }
}

function renderAll() {
  const snapshot = state.snapshot;
  if (!snapshot) return;
  $("runStatus").textContent = snapshot.run.status;
  $("runStatus").className = `status-chip ${snapshot.run.status}`;
  renderTimeline();
  renderApproval();
  renderInspector();
}

function renderTimeline() {
  const events = state.snapshot?.events || [];
  $("timelineEmpty").hidden = events.length > 0;
  $("eventTimeline").hidden = events.length === 0;
  $("eventTimeline").innerHTML = events.map((event, index) => `
    <li class="event-item ${eventCategory(event.type)} ${index <= state.revealedEventIndex ? "revealed" : ""} ${index === state.selectedEventIndex ? "selected" : ""}" data-event-index="${index}">
      <span class="event-dot"></span>
      <button type="button" class="event-button">
        <span class="event-type">${escapeHtml(event.type)}</span>
        <span class="event-summary">${escapeHtml(event.teaching.summary)}</span>
        <span class="event-sequence">#${event.sequence}</span>
      </button>
    </li>
  `).join("");
  document.querySelectorAll("[data-event-index]").forEach((item) => {
    item.addEventListener("click", () => {
      stopAutoplay();
      state.followLive = false;
      state.selectedEventIndex = Number(item.dataset.eventIndex);
      state.revealedEventIndex = Math.max(state.revealedEventIndex, state.selectedEventIndex);
      state.activeTab = "event";
      syncTabs();
      renderTimeline();
      renderInspector();
    });
  });
  const selected = document.querySelector(".event-item.selected");
  selected?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderApproval() {
  const approval = state.snapshot?.pending_approval;
  const card = $("approvalCard");
  card.hidden = !approval;
  if (!approval) return;
  const args = JSON.stringify(approval.tool_call.arguments, null, 0);
  $("approvalSummary").textContent = `${approval.tool_call.name} ${args}`;
  $("approveButton").dataset.approvalId = approval.id;
  $("rejectButton").dataset.approvalId = approval.id;
}

function syncTabs() {
  document.querySelectorAll("#inspectorTabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
}

function renderInspector() {
  if (!state.snapshot) return;
  syncTabs();
  const renderers = {
    event: renderEventInspector,
    state: renderStateInspector,
    messages: renderMessagesInspector,
    execution: renderExecutionInspector,
    trace: renderTraceInspector,
    sqlite: renderSqliteInspector,
    acceptance: renderAcceptanceInspector,
  };
  $("inspectorContent").innerHTML = renderers[state.activeTab]();
}

function renderEventInspector() {
  const event = state.snapshot.events[state.selectedEventIndex];
  if (!event) return '<div class="empty-inspector">选择一个事件查看教学解释。</div>';
  const changed = Object.keys(event.state_after).filter(
    (key) => JSON.stringify(event.state_before[key]) !== JSON.stringify(event.state_after[key])
  );
  const diff = changed.length
    ? changed.map((key) => `<div class="diff-row"><span class="key">${escapeHtml(key)}</span><span class="diff-change">${escapeHtml(JSON.stringify(event.state_before[key]))} → ${escapeHtml(JSON.stringify(event.state_after[key]))}</span></div>`).join("")
    : '<div class="diff-row"><span class="key">状态</span><span>领域状态未变化，仅新增可观察事实</span></div>';
  return `
    <section class="detail-block"><span class="detail-label">SEQUENCE #${event.sequence}</span><h3>${escapeHtml(event.teaching.title)}</h3><p>${escapeHtml(event.teaching.summary)}</p></section>
    <section class="detail-block"><span class="detail-label">为什么需要它</span><p>${escapeHtml(event.teaching.why)}</p></section>
    <section class="detail-block"><span class="detail-label">下一步</span><p>${escapeHtml(event.teaching.next)}</p></section>
    <section class="detail-block"><span class="detail-label">状态变化</span><div class="state-diff">${diff}</div></section>
    <section class="detail-block"><span class="detail-label">源码路径</span><div class="code-list">${event.teaching.code.map((item) => `<div class="code-item">${escapeHtml(item)}</div>`).join("")}</div></section>
    <section class="detail-block"><span class="detail-label">事件 Payload</span><pre class="json-view">${json(event.payload)}</pre></section>
  `;
}

function renderStateInspector() {
  const run = state.snapshot.run;
  const event = state.snapshot.events[state.selectedEventIndex];
  const projected = event?.state_after || {};
  return `
    <section class="detail-block"><span class="detail-label">AGENT RUN</span><div class="kv-grid">
      ${kv("Run ID", run.id)}${kv("Trace ID", run.metadata.trace_id)}${kv("Agent", run.agent_name)}${kv("Status", run.status)}${kv("Steps", run.step_count)}${kv("Tool Calls", run.tool_call_count)}
    </div></section>
    <section class="detail-block"><span class="detail-label">当前结果</span><pre class="json-view">${escapeHtml(run.result || run.error || "尚未生成最终结果")}</pre></section>
    <section class="detail-block"><span class="detail-label">回放到当前事件的投影状态</span><pre class="json-view">${json(projected)}</pre></section>
    <section class="detail-block"><span class="detail-label">Run Metadata</span><pre class="json-view">${json(run.metadata)}</pre></section>
  `;
}

function renderMessagesInspector() {
  const checkpoint = state.snapshot.checkpoint;
  if (!checkpoint) return '<div class="empty-inspector">当前还没有 Checkpoint。</div>';
  return `
    <section class="detail-block"><span class="detail-label">LATEST CHECKPOINT · STEP ${checkpoint.step}</span>
      <div class="message-list">${checkpoint.messages.map((message) => `
        <div class="message"><span class="message-role">${escapeHtml(message.role)}${message.name ? ` · ${escapeHtml(message.name)}` : ""}</span><div class="message-content">${escapeHtml(message.content || "[结构化 ToolCall]")}</div>${message.tool_calls?.length ? `<pre class="json-view">${json(message.tool_calls)}</pre>` : ""}</div>
      `).join("")}</div>
    </section>
  `;
}

function renderExecutionInspector() {
  const steps = state.snapshot.steps;
  const tools = state.snapshot.tool_executions;
  return `
    <section class="detail-block"><span class="detail-label">MODEL STEPS</span><div class="execution-list">
      ${steps.length ? steps.map((step) => `<div class="execution-row"><div class="execution-row-top"><strong>Step ${step.step_index}</strong><small>${escapeHtml(step.status)}</small></div><pre class="json-view">${json(step.assistant_message)}</pre></div>`).join("") : '<div class="empty-inspector">尚无 Step</div>'}
    </div></section>
    <section class="detail-block"><span class="detail-label">TOOL EXECUTIONS</span><div class="execution-list">
      ${tools.length ? tools.map((tool) => `<div class="execution-row"><div class="execution-row-top"><strong>${escapeHtml(tool.tool_call.name)}</strong><small>${escapeHtml(tool.status)}</small></div><pre class="json-view">${json({ id: tool.id, arguments: tool.tool_call.arguments, idempotency_key: tool.idempotency_key, requires_approval: tool.requires_approval, result: tool.result_content, error: tool.error })}</pre></div>`).join("") : '<div class="empty-inspector">该场景没有工具执行。</div>'}
    </div></section>
  `;
}

function renderTraceInspector() {
  const trace = state.snapshot.trace;
  const metrics = state.snapshot.metrics;
  return `
    <section class="detail-block"><span class="detail-label">TRACE SUMMARY</span><div class="kv-grid">${kv("Trace ID", trace.trace_id)}${kv("Spans", trace.spans.length)}${kv("Run Status", trace.status)}${kv("Events", trace.events.length)}</div></section>
    <section class="detail-block"><span class="detail-label">SPANS</span><div class="execution-list">${trace.spans.map((span) => `<div class="span-row"><div class="span-row-top"><strong>${escapeHtml(span.name)}</strong><small>${span.duration_ms == null ? "running" : `${span.duration_ms.toFixed(2)} ms`}</small></div><small>${escapeHtml(span.kind)} · ${escapeHtml(span.status)} · #${span.start_sequence}→${span.end_sequence ?? "…"}</small></div>`).join("")}</div></section>
    <section class="detail-block"><span class="detail-label">GLOBAL METRICS</span><div class="metrics-grid">${metric("Runs", metrics.total_runs)}${metric("Events", metrics.total_events)}${metric("Model", metrics.model_requests)}${metric("Tools", metrics.tool_requests)}${metric("Approvals", metrics.approval_requests)}${metric("Tokens", metrics.tokens.total)}</div></section>
  `;
}

function renderSqliteInspector() {
  const persistence = state.snapshot.persistence;
  return `
    <section class="detail-block"><span class="detail-label">PERSISTENCE SOURCE</span><pre class="json-view">${escapeHtml(persistence.database)}</pre></section>
    <section class="detail-block"><span class="detail-label">SCHEMA VERSION</span><div class="kv-grid">${kv("Migration", persistence.schema_version)}${kv("事实来源", "SQLite")}</div></section>
    <section class="detail-block"><span class="detail-label">本次 RUN 相关记录</span><div class="kv-grid">${Object.entries(persistence.tables).map(([key, value]) => kv(key, value)).join("")}</div></section>
    <section class="detail-block"><span class="detail-label">理解重点</span><p>Learning Console 不维护第二份运行状态。页面展示的 Run、Event、Step、ToolExecution、Approval 和 Checkpoint 都来自 Runtime 的持久化事实。</p></section>
  `;
}

function renderAcceptanceInspector() {
  const acceptance = state.snapshot.acceptance;
  const waiting = acceptance.waiting_for_human;
  return `
    <div class="acceptance-banner ${acceptance.passed ? "passed" : ""}">${acceptance.passed ? "✓ 场景已通过自动验收" : waiting ? "等待你完成审批后继续验收" : "场景仍在执行或存在未满足条件"}</div>
    <div class="check-list">${acceptance.checks.map((check) => `<div class="check-row ${check.passed ? "passed" : ""}"><span class="check-icon">${check.passed ? "✓" : "×"}</span><span class="check-copy"><strong>${escapeHtml(check.name)}</strong><small>${escapeHtml(check.detail)}</small></span></div>`).join("")}</div>
    <section class="detail-block" style="margin-top:14px"><span class="detail-label">EXPECTED EVENT PATH</span><div class="code-list">${state.snapshot.scenario.expected_events.map((event) => `<div class="code-item">${escapeHtml(event)}</div>`).join("")}</div></section>
  `;
}

function kv(label, value) {
  return `<div class="kv-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`;
}
function metric(label, value) {
  return `<div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function replayFromStart() {
  if (!state.snapshot?.events.length) return;
  stopAutoplay();
  state.followLive = false;
  state.revealedEventIndex = 0;
  state.selectedEventIndex = 0;
  state.activeTab = "event";
  renderAll();
}

function stepEvent(direction) {
  if (!state.snapshot?.events.length) return;
  stopAutoplay();
  state.followLive = false;
  const last = state.snapshot.events.length - 1;
  const next = Math.max(0, Math.min(last, state.selectedEventIndex + direction));
  state.selectedEventIndex = next;
  state.revealedEventIndex = Math.max(state.revealedEventIndex, next);
  state.activeTab = "event";
  renderAll();
}

function toggleAutoplay() {
  if (state.autoplay) { stopAutoplay(); return; }
  if (!state.snapshot?.events.length) return;
  state.followLive = false;
  if (state.selectedEventIndex >= state.snapshot.events.length - 1) {
    state.selectedEventIndex = -1;
    state.revealedEventIndex = -1;
  }
  $("playButton").textContent = "Ⅱ";
  const tick = () => {
    const last = state.snapshot.events.length - 1;
    if (state.selectedEventIndex >= last) { stopAutoplay(); return; }
    state.selectedEventIndex += 1;
    state.revealedEventIndex = state.selectedEventIndex;
    state.activeTab = "event";
    renderAll();
  };
  tick();
  state.autoplay = window.setInterval(tick, Number($("speedSelect").value));
}

function stopAutoplay() {
  if (state.autoplay) window.clearInterval(state.autoplay);
  state.autoplay = null;
  $("playButton").textContent = "▶";
}

async function resolveApproval(approved) {
  const approval = state.snapshot?.pending_approval;
  if (!approval) return;
  $("approveButton").disabled = true;
  $("rejectButton").disabled = true;
  setConnection("live", approved ? "正在批准并恢复" : "正在拒绝并恢复");
  try {
    await request(`/lab/api/approvals/${approval.id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ approved, reason: approved ? "Learning Console 手动批准" : "Learning Console 手动拒绝" }),
    });
    state.followLive = true;
    await refreshSnapshot();
    if (!terminalStatuses.has(state.snapshot.run.status)) connectEventSource(state.runId);
    toast(approved ? "审批已通过，Runtime 已恢复" : "审批已拒绝，Runtime 已记录结论");
  } catch (error) {
    toast(error.message);
  } finally {
    $("approveButton").disabled = false;
    $("rejectButton").disabled = false;
  }
}

function bindEvents() {
  $("startButton").addEventListener("click", startScenario);
  $("replayButton").addEventListener("click", replayFromStart);
  $("prevButton").addEventListener("click", () => stepEvent(-1));
  $("nextButton").addEventListener("click", () => stepEvent(1));
  $("playButton").addEventListener("click", toggleAutoplay);
  $("speedSelect").addEventListener("change", () => { if (state.autoplay) { stopAutoplay(); toggleAutoplay(); } });
  $("approveButton").addEventListener("click", () => resolveApproval(true));
  $("rejectButton").addEventListener("click", () => resolveApproval(false));
  document.querySelectorAll("#inspectorTabs button").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      syncTabs();
      renderInspector();
    });
  });
}

async function init() {
  bindEvents();
  try {
    await loadScenarios();
    setConnection("idle", "选择场景开始");
  } catch (error) {
    setConnection("error", "加载失败");
    toast(error.message);
  }
}

document.addEventListener("DOMContentLoaded", init);
