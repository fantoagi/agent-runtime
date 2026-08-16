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
  poller: null,
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
  "workflow.started", "workflow.completed", "workflow.failed", "workflow.cancelled",
  "delegation.created", "delegation.completed", "delegation.failed", "delegation.cancelled",
  "session.run.attached", "memory.created", "memory.deleted", "memory.search.started",
  "memory.search.completed", "context.built", "context.compacted", "tool.result.artifactized",
];

const domainSwimlanes = [
  { id: "run", label: "Run", detail: "Root 生命周期", color: "var(--blue)" },
  { id: "memory", label: "Session / Memory", detail: "会话与作用域记忆", color: "#f78cda" },
  { id: "context", label: "Context", detail: "预算、选择与压缩", color: "#9fe870" },
  { id: "model", label: "Model", detail: "推理与流式输出", color: "var(--purple)" },
  { id: "tool", label: "Tool", detail: "工具执行与 Artifact", color: "var(--orange)" },
  { id: "approval", label: "Approval", detail: "人工决策", color: "var(--yellow)" },
  { id: "checkpoint", label: "State", detail: "Step / Checkpoint", color: "var(--green)" },
];
const agentLaneColors = ["#66d9ef", "#b596ff", "#ffad66", "#79e6b3", "#f78cda", "#66a9ff"];
const swimlaneLayout = {
  columnWidth: 156,
  headerHeight: 48,
  rowHeight: 76,
};

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

function eventLane(value) {
  const event = typeof value === "string" ? { type: value } : value;
  const type = event.type || "";
  if (event.run_role === "child" && event.run_id) return `agent:${event.run_id}`;
  if (type.startsWith("workflow.") || type.startsWith("delegation.") || type.startsWith("run.")) return "run";
  if (type.startsWith("session.") || type.startsWith("memory.")) return "memory";
  if (type.startsWith("context.")) return "context";
  if (type.startsWith("model.")) return "model";
  if (type.startsWith("tool.")) return "tool";
  if (type.startsWith("approval.")) return "approval";
  if (type.startsWith("checkpoint.") || type.startsWith("step.")) return "checkpoint";
  return "run";
}

function eventCategory(value) {
  const event = typeof value === "string" ? { type: value } : value;
  if ((event.type || "").includes("failed") || (event.type || "").includes("unknown")) return "error";
  if (event.run_role === "child") return "agent";
  return eventLane(event);
}

function workflowPosition(run) {
  const metadata = run.metadata || {};
  if (Number.isFinite(metadata.workflow_step)) return metadata.workflow_step;
  if (Number.isFinite(metadata.workflow_branch)) return metadata.workflow_branch;
  return Number.MAX_SAFE_INTEGER;
}

function buildSwimlanes(snapshot) {
  const runs = snapshot?.runs || [];
  const events = snapshot?.events || [];
  const root = snapshot?.run || runs.find((run) => run.run_role === "root") || {};
  const children = runs
    .filter((run) => run.run_role === "child")
    .sort((left, right) => {
      const position = workflowPosition(left) - workflowPosition(right);
      if (position !== 0) return position;
      return String(left.agent_name || left.id).localeCompare(String(right.agent_name || right.id));
    });
  const workflowType = root.metadata?.workflow_type;
  const rootLane = {
    ...domainSwimlanes[0],
    label: children.length ? "Workflow Parent" : "Run",
    detail: children.length
      ? `${workflowType || "workflow"} · ${root.status || "unknown"}`
      : `${root.agent_name || "Runtime"} · ${root.status || "unknown"}`,
    kind: "root",
    runId: root.id,
  };
  const childLanes = children.map((run, index) => {
    const metadata = run.metadata || {};
    const isSequential = Number.isFinite(metadata.workflow_step);
    const position = isSequential ? metadata.workflow_step : metadata.workflow_branch;
    const prefix = Number.isFinite(position)
      ? `${isSequential ? "Step" : "Branch"} ${position + 1}`
      : `Child ${index + 1}`;
    return {
      id: `agent:${run.id}`,
      label: metadata.workflow_step_name || run.agent_name || `Child Agent ${index + 1}`,
      detail: `${prefix} · ${run.agent_name || "Agent"} · ${run.status || "unknown"}`,
      color: agentLaneColors[index % agentLaneColors.length],
      kind: "agent",
      runId: run.id,
    };
  });
  const activeDomainLanes = domainSwimlanes.slice(1).filter((lane) =>
    events.some((event) => eventLane(event) === lane.id)
  );
  return [rootLane, ...childLanes, ...activeDomainLanes];
}

function relativeEventTime(events, index) {
  if (!events.length || !events[index]?.timestamp) return "+0ms";
  const start = Date.parse(events[0].timestamp);
  const current = Date.parse(events[index].timestamp);
  const elapsed = Math.max(0, current - start);
  if (elapsed < 1000) return `+${elapsed}ms`;
  return `+${(elapsed / 1000).toFixed(elapsed < 10000 ? 1 : 0)}s`;
}

function swimlanePoint(eventIndex, laneId, swimlanes) {
  const laneIndex = Math.max(0, swimlanes.findIndex((lane) => lane.id === laneId));
  return {
    x: eventIndex * swimlaneLayout.columnWidth + swimlaneLayout.columnWidth / 2,
    y: swimlaneLayout.headerHeight + laneIndex * swimlaneLayout.rowHeight + swimlaneLayout.rowHeight / 2,
  };
}

function laneColor(swimlanes, laneId) {
  return swimlanes.find((lane) => lane.id === laneId)?.color || "var(--line-strong)";
}

function buildTimelineLinks(events) {
  const links = [];
  const eventsByRun = new Map();
  events.forEach((event, index) => {
    const current = eventsByRun.get(event.run_id) || [];
    current.push({ event, index });
    eventsByRun.set(event.run_id, current);
  });
  eventsByRun.forEach((runEvents) => {
    runEvents.sort((left, right) => left.event.local_sequence - right.event.local_sequence);
    runEvents.slice(1).forEach((current, offset) => {
      links.push({
        fromIndex: runEvents[offset].index,
        toIndex: current.index,
        kind: "run-flow",
        category: eventCategory(current.event),
        laneId: eventLane(current.event),
      });
    });
  });

  const childCreated = new Map();
  const childTerminal = new Map();
  const delegationCreated = new Map();
  const delegationResolved = new Map();
  events.forEach((event, index) => {
    if (event.run_role === "child" && event.type === "run.created") childCreated.set(event.run_id, index);
    if (event.run_role === "child" && ["run.completed", "run.failed", "run.cancelled"].includes(event.type)) {
      childTerminal.set(event.run_id, index);
    }
    const childRunId = event.payload?.child_run_id;
    if (!childRunId) return;
    if (event.type === "delegation.created") delegationCreated.set(childRunId, index);
    if (["delegation.completed", "delegation.failed", "delegation.cancelled"].includes(event.type)) {
      delegationResolved.set(childRunId, index);
    }
  });
  delegationCreated.forEach((fromIndex, childRunId) => {
    if (!childCreated.has(childRunId)) return;
    links.push({
      fromIndex,
      toIndex: childCreated.get(childRunId),
      kind: "delegation-flow",
      category: "agent",
      laneId: `agent:${childRunId}`,
    });
  });
  delegationResolved.forEach((toIndex, childRunId) => {
    if (!childTerminal.has(childRunId)) return;
    links.push({
      fromIndex: childTerminal.get(childRunId),
      toIndex,
      kind: "aggregation-flow",
      category: "agent",
      laneId: `agent:${childRunId}`,
    });
  });
  return links;
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
  if (state.poller) window.clearInterval(state.poller);
  state.poller = null;
}

function startSnapshotPolling() {
  if (state.poller) window.clearInterval(state.poller);
  state.poller = window.setInterval(async () => {
    if (!state.runId || terminalStatuses.has(state.snapshot?.run?.status)) return;
    try { await refreshSnapshot(); } catch (_) { /* SSE remains the primary signal */ }
  }, 450);
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

function renderSwimlaneLabels(swimlanes) {
  const labels = $("swimlaneLabels");
  labels.style.gridTemplateRows = `${swimlaneLayout.headerHeight}px repeat(${swimlanes.length}, ${swimlaneLayout.rowHeight}px)`;
  labels.innerHTML = `
    <div class="swimlane-corner"><span>FLOW</span><small>时间 →</small></div>
    ${swimlanes.map((lane) => `
      <div class="swimlane-label ${lane.kind === "agent" ? "agent" : lane.id}" data-lane-id="${escapeHtml(lane.id)}" style="--lane-color:${lane.color}">
        <strong>${escapeHtml(lane.label)}</strong>
        <small>${escapeHtml(lane.detail)}</small>
      </div>
    `).join("")}
  `;
}

function timelineLinkPath(link, swimlanes) {
  const fromEvent = state.snapshot.events[link.fromIndex];
  const toEvent = state.snapshot.events[link.toIndex];
  const from = swimlanePoint(link.fromIndex, eventLane(fromEvent), swimlanes);
  const to = swimlanePoint(link.toIndex, eventLane(toEvent), swimlanes);
  const middle = (from.x + to.x) / 2;
  return `M ${from.x} ${from.y} C ${middle} ${from.y}, ${middle} ${to.y}, ${to.x} ${to.y}`;
}

function renderTimeline() {
  const events = state.snapshot?.events || [];
  const hasEvents = events.length > 0;
  $("timelineEmpty").hidden = hasEvents;
  $("swimlaneBoard").hidden = !hasEvents;
  if (!hasEvents) {
    $("eventTimeline").innerHTML = "";
    $("swimlaneLabels").innerHTML = "";
    $("swimlaneStatus").textContent = "0 / 0 事件";
    return;
  }

  const swimlanes = buildSwimlanes(state.snapshot);
  const agentLaneCount = swimlanes.filter((lane) => lane.kind === "agent").length;
  const revealedCount = Math.max(0, Math.min(events.length, state.revealedEventIndex + 1));
  $("swimlaneStatus").textContent = `${revealedCount} / ${events.length} 事件 · ${agentLaneCount} 条 Agent 泳道`;

  const canvasWidth = Math.max(3, events.length) * swimlaneLayout.columnWidth;
  const canvasHeight = swimlaneLayout.headerHeight + swimlanes.length * swimlaneLayout.rowHeight;
  const board = $("swimlaneBoard");
  board.style.height = `${canvasHeight}px`;
  renderSwimlaneLabels(swimlanes);

  const tracks = swimlanes.map((lane, laneIndex) => `
    <div class="swimlane-track ${lane.kind === "agent" ? "agent" : lane.id}" data-lane-id="${escapeHtml(lane.id)}" style="--lane-color:${lane.color};top:${swimlaneLayout.headerHeight + laneIndex * swimlaneLayout.rowHeight}px;height:${swimlaneLayout.rowHeight}px"></div>
  `).join("");
  const ticks = events.map((event, index) => `
    <div class="swimlane-tick ${index <= state.revealedEventIndex ? "revealed" : ""} ${index === state.selectedEventIndex ? "selected" : ""}" style="left:${index * swimlaneLayout.columnWidth}px;width:${swimlaneLayout.columnWidth}px">
      <strong>#${event.timeline_sequence || index + 1}</strong><small>${relativeEventTime(events, index)}</small>
    </div>
  `).join("");
  const links = buildTimelineLinks(events).map((link) => {
    const revealIndex = Math.max(link.fromIndex, link.toIndex);
    const selected = [link.fromIndex, link.toIndex].includes(state.selectedEventIndex);
    const color = link.category === "error" ? "var(--red)" : laneColor(swimlanes, link.laneId);
    return `<path class="swimlane-link ${link.kind} ${link.category} ${revealIndex <= state.revealedEventIndex ? "revealed" : ""} ${selected ? "selected" : ""}" style="--lane-color:${color}" d="${timelineLinkPath(link, swimlanes)}" />`;
  }).join("");
  const nodes = events.map((event, index) => {
    const lane = eventLane(event);
    const laneIndex = Math.max(0, swimlanes.findIndex((item) => item.id === lane));
    const left = index * swimlaneLayout.columnWidth + 12;
    const top = swimlaneLayout.headerHeight + laneIndex * swimlaneLayout.rowHeight + 9;
    const category = eventCategory(event);
    const color = category === "error" ? "var(--red)" : laneColor(swimlanes, lane);
    const classes = [
      "swimlane-event",
      category,
      index <= state.revealedEventIndex ? "revealed" : "",
      index === state.revealedEventIndex ? "arriving" : "",
      index === state.selectedEventIndex ? "selected" : "",
    ].filter(Boolean).join(" ");
    return `
      <button type="button" class="${classes}" style="--lane-color:${color};left:${left}px;top:${top}px" data-event-index="${index}" data-run-id="${escapeHtml(event.run_id)}" title="${escapeHtml(event.teaching.summary)}" aria-label="#${event.timeline_sequence || event.sequence} ${escapeHtml(event.type)}">
        <span class="swimlane-event-top"><span class="swimlane-event-dot"></span><span>#${event.timeline_sequence || event.sequence}</span><small>${event.run_role === "child" ? `Local #${event.local_sequence}` : relativeEventTime(events, index)}</small></span>
        <strong>${escapeHtml(event.type)}</strong>
        <span class="swimlane-event-agent">${escapeHtml(event.agent_name || "Runtime")}</span>
        <span class="swimlane-event-title">${escapeHtml(event.teaching.title)}</span>
      </button>
    `;
  }).join("");

  const canvas = $("eventTimeline");
  canvas.style.width = `${canvasWidth}px`;
  canvas.style.height = `${canvasHeight}px`;
  canvas.innerHTML = `
    ${tracks}
    <svg class="swimlane-links" width="${canvasWidth}" height="${canvasHeight}" viewBox="0 0 ${canvasWidth} ${canvasHeight}" aria-hidden="true">${links}</svg>
    <div class="swimlane-axis">${ticks}</div>
    ${nodes}
  `;

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

  const selected = document.querySelector(".swimlane-event.selected");
  const viewport = $("swimlaneViewport");
  if (selected && viewport) {
    const target = selected.offsetLeft - viewport.clientWidth / 2 + selected.offsetWidth / 2;
    viewport.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
  }
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
    context: renderContextInspector,
    memory: renderMemoryInspector,
    artifacts: renderArtifactInspector,
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
    <section class="detail-block"><span class="detail-label">TIMELINE #${event.timeline_sequence || event.sequence} · ${escapeHtml(event.agent_name || "Runtime")} · LOCAL #${event.local_sequence || event.sequence}</span><h3>${escapeHtml(event.teaching.title)}</h3><p>${escapeHtml(event.teaching.summary)}</p></section>
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
  const tree = state.snapshot.trace_tree;
  const metrics = state.snapshot.metrics;
  const renderNode = (node, depth = 0) => `
    <div class="topology-node" style="--depth:${depth}">
      <div><strong>${escapeHtml(node.run.agent_name)}</strong><small>${escapeHtml(node.run.status)} · ${escapeHtml(node.run.id)}</small></div>
      ${node.relation ? `<span>${escapeHtml(node.relation.relation_type)} · ${escapeHtml(node.relation.delegation_key)}</span>` : '<span>root</span>'}
    </div>${node.children.map((child) => renderNode(child, depth + 1)).join("")}`;
  return `
    <section class="detail-block"><span class="detail-label">PARENT / CHILD TOPOLOGY</span><div class="topology-tree">${renderNode(tree.root)}</div></section>
    <section class="detail-block"><span class="detail-label">TRACE SUMMARY</span><div class="kv-grid">${kv("Root Trace", tree.root_trace_id)}${kv("Tree Nodes", tree.node_count)}${kv("Root Status", trace.status)}${kv("Root Events", trace.events.length)}</div></section>
    <section class="detail-block"><span class="detail-label">ROOT SPANS</span><div class="execution-list">${trace.spans.map((span) => `<div class="span-row"><div class="span-row-top"><strong>${escapeHtml(span.name)}</strong><small>${span.duration_ms == null ? "running" : `${span.duration_ms.toFixed(2)} ms`}</small></div><small>${escapeHtml(span.kind)} · ${escapeHtml(span.status)} · #${span.start_sequence}→${span.end_sequence ?? "…"}</small></div>`).join("")}</div></section>
    <section class="detail-block"><span class="detail-label">GLOBAL METRICS</span><div class="metrics-grid">${metric("Runs", metrics.total_runs)}${metric("Child", metrics.multi_agent.child_runs)}${metric("Workflow", metrics.multi_agent.workflow_runs)}${metric("Delegations", metrics.multi_agent.delegations)}${metric("Sessions", metrics.context_memory.sessions)}${metric("Memories", metrics.context_memory.memories_active)}${metric("Searches", metrics.context_memory.memory_searches)}${metric("Compactions", metrics.context_memory.context_compactions)}${metric("Tokens", metrics.tokens.total)}</div></section>
  `;
}

function renderContextInspector() {
  const builds = state.snapshot.context_builds || [];
  if (!builds.length) return '<div class="empty-inspector">该场景还没有 Context 构建事件。</div>';
  return `<section class="detail-block"><span class="detail-label">CONTEXT BUILDS</span><div class="execution-list">${builds.map((item) => `
    <div class="execution-row"><div class="execution-row-top"><strong>${escapeHtml(item.event_type)} · ${escapeHtml(item.agent_name)}</strong><small>#${item.timeline_sequence}</small></div>
    <div class="kv-grid">${kv("Budget", item.token_budget)}${kv("Original", item.original_tokens)}${kv("Estimated", item.estimated_tokens)}${kv("Omitted", item.omitted_messages)}${kv("Memory IDs", (item.memory_ids || []).length)}${kv("Overflow", item.overflow)}</div>
    ${item.summary ? `<pre class="json-view">${escapeHtml(item.summary)}</pre>` : ""}</div>`).join("")}</div></section>`;
}

function renderMemoryInspector() {
  const session = state.snapshot.session;
  const memories = state.snapshot.memories || [];
  return `
    <section class="detail-block"><span class="detail-label">SESSION</span>${session ? `<div class="kv-grid">${kv("Session ID", session.id)}${kv("Runs", state.snapshot.session_runs.length)}</div><pre class="json-view">${json(session.metadata)}</pre>` : '<div class="empty-inspector">该场景没有 Session。</div>'}</section>
    <section class="detail-block"><span class="detail-label">SCOPED MEMORIES</span><div class="execution-list">${memories.length ? memories.map((item) => `<div class="execution-row"><div class="execution-row-top"><strong>${escapeHtml(item.scope)} · ${escapeHtml(item.scope_id)}</strong><small>${item.active ? "active" : "inactive"}</small></div><p>${escapeHtml(item.content)}</p><pre class="json-view">${json({ id: item.id, source_run_id: item.source_run_id, source_trace_id: item.source_trace_id, expires_at: item.expires_at, metadata: item.metadata })}</pre></div>`).join("") : '<div class="empty-inspector">该场景没有 Memory。</div>'}</div></section>`;
}

function renderArtifactInspector() {
  const artifacts = state.snapshot.artifacts || [];
  if (!artifacts.length) return '<div class="empty-inspector">该场景还没有 Artifact。</div>';
  return `<section class="detail-block"><span class="detail-label">TOOL RESULT ARTIFACTS</span><div class="execution-list">${artifacts.map((item) => `<div class="execution-row"><div class="execution-row-top"><strong>${escapeHtml(item.agent_name)}</strong><small>${item.exists ? "file exists" : "missing"}</small></div><div class="kv-grid">${kv("Characters", item.characters)}${kv("Preview chars", item.preview_characters)}${kv("Execution", item.tool_execution_id)}${kv("Run", item.run_id)}</div><pre class="json-view">${escapeHtml(item.path)}</pre><pre class="json-view">${escapeHtml(item.preview)}</pre></div>`).join("")}</div></section>`;
}
function renderSqliteInspector() {
  const persistence = state.snapshot.persistence;
  const reliability = state.snapshot.reliability || {};
  const sqlite = reliability.sqlite || {};
  const doctor = reliability.doctor || {};
  const capacity = reliability.capacity || {};
  const backup = reliability.backup || {};
  return `
    <section class="detail-block"><span class="detail-label">RELIABILITY STATUS</span><div class="kv-grid">${kv("Run", reliability.run_health)}${kv("Runtime 接受请求", reliability.runtime_accepting ? "yes" : "no")}${kv("活动任务", `${capacity.active_tasks ?? 0} / ${capacity.max_inflight_runs ?? "-"}`)}${kv("模型并发上限", capacity.max_concurrent_model_requests ?? "-")}${kv("SQLite", sqlite.status)}${kv("UNKNOWN Tool", reliability.unknown_tool_executions ?? 0)}${kv("Doctor", doctor.status || "unknown")}${kv("Backup format", backup.format_version ?? "-")}${kv("Restore", backup.restore_requires_shutdown ? "offline only" : "unknown")}</div><p>${escapeHtml(reliability.guidance || "")}</p></section>
    <section class="detail-block"><span class="detail-label">PERSISTENCE SOURCE</span><pre class="json-view">${escapeHtml(persistence.database)}</pre></section>
    <section class="detail-block"><span class="detail-label">SCHEMA VERSION</span><div class="kv-grid">${kv("Migration", persistence.schema_version)}${kv("Journal", sqlite.journal_mode)}${kv("事实来源", "SQLite")}</div></section>
    <section class="detail-block"><span class="detail-label">本次 RUN 相关记录</span><div class="kv-grid">${Object.entries(persistence.tables).map(([key, value]) => kv(key, value)).join("")}</div></section>
    <section class="detail-block"><span class="detail-label">可靠性说明</span><p>Learning Console 展示持久化的 Run、Event、Step、ToolExecution、Approval 和 Checkpoint。Runtime 不接受请求、Run 为 failed/cancelled，或存在 UNKNOWN Tool 时都需要人工检查；UNKNOWN 必须先确认结果，再显式恢复 Run。备份恢复位于执行循环之外，恢复前必须停止 Runtime；演练命令：${escapeHtml(backup.drill_command || "python scripts/run_backup_recovery.py")}。</p></section>
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
