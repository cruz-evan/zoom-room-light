const STATE_KEY = "current-state";
const DEVICE_STATE_PREFIX = "current-state:";
const ZOOM_WEBHOOK_HISTORY_PREFIX = "zoom-webhook:";
const ZOOM_WEBHOOK_HISTORY_TTL_SECONDS = 7 * 24 * 60 * 60;
const DEFAULT_POLL_SECONDS = 5;
const SIGNATURE_TOLERANCE_SECONDS = 300;
const DEFAULT_ACTIVE_MEETING_LOOKAHEAD_MINUTES = 5;
const DEFAULT_EMPTY_ROOM_LOOKAHEAD_MINUTES = 15;
const DEFAULT_ENDING_SOON_MINUTES = 5;
const DEFAULT_SCHEDULE_END_CLEAR_GRACE_MINUTES = 5;
const DEFAULT_CALENDAR_LOOKBACK_MINUTES = 720;
const DEFAULT_CALENDAR_LOOKAHEAD_MINUTES = 240;
const MICROSOFT_GRAPH_BASE = "https://graph.microsoft.com/v1.0";
const DEFAULT_OTA_UPSTREAM_BASE_URL = "https://cruz-evan.github.io/zoom-room-light";

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

let microsoftTokenCache = null;

export default {
  async fetch(request, env, ctx) {
    try {
      return await handleRequest(request, env, ctx);
    } catch (error) {
      logRelayEvent("worker_error", { error: errorMessage(error) });
      return jsonResponse({ error: "internal_error" }, 500);
    }
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runSchedulePoll(env, { reason: `cron:${controller.cron}` }));
  },
};

export async function handleRequest(request, env, ctx) {
  const url = new URL(request.url);
  const path = stripTrailingSlash(url.pathname);

  if (request.method === "GET" && path === "/health") {
    return jsonResponse({ ok: true });
  }

  if (request.method === "GET" && path.startsWith("/ota/")) {
    return handleOtaProxy(request, env, url, path);
  }

  const deviceId = deviceIdFromRequest(request, url, path);
  if (request.method === "GET" && deviceId !== null) {
    if (!authorizeBearer(request, devicePollToken(env))) {
      return jsonResponse({ error: "unauthorized" }, 401);
    }

    const assignment = roomAssignmentForDevice(env, deviceId);
    const state = await readState(env, assignment ? assignment.device_id : "");
    logRelayEvent("device_state_read", {
      device_id: deviceId,
      physical_room_name: assignment?.physical_room_name || "",
      zoom_meeting_room_name: assignment?.zoom_meeting_room_name || "",
      microsoft_calendar_user_id: assignment?.microsoft_calendar_user_id || "",
      poll_seconds: pollSeconds(env),
      state: logStateFields(state),
    });
    return jsonResponse(deviceStateResponse(state, env, deviceId, assignment));
  }

  if ((request.method === "GET" || request.method === "POST") && path === "/schedule/check") {
    if (!env.ADMIN_TOKEN) {
      return jsonResponse({ error: "schedule_check_disabled" }, 404);
    }
    if (!authorizeBearer(request, env.ADMIN_TOKEN)) {
      return jsonResponse({ error: "unauthorized" }, 401);
    }

    const result = await runSchedulePoll(env, { reason: "manual" });
    return jsonResponse(result, result.ok ? 200 : 502);
  }

  const zoomWebhookDeviceId = zoomWebhookDeviceIdFromRequest(url, path);
  if (request.method === "POST" && zoomWebhookDeviceId !== null) {
    return handleZoomWebhook(request, env, ctx, zoomWebhookDeviceId);
  }

  if ((request.method === "GET" || request.method === "POST") && path.startsWith("/simulate/")) {
    return handleSimulate(path, request, env);
  }

  return jsonResponse({ error: "not_found" }, 404);
}

async function handleOtaProxy(request, env, requestUrl, path) {
  const upstreamBase = otaUpstreamBaseUrl(env);
  const upstreamPath = otaUpstreamPath(path);
  if (!upstreamPath) {
    return jsonResponse({ error: "not_found" }, 404);
  }

  const upstreamUrl = new URL(upstreamBase + upstreamPath + requestUrl.search);
  const upstreamResponse = await fetch(upstreamUrl.toString(), {
    headers: otaUpstreamHeaders(request, env),
  });

  if (!upstreamResponse.ok) {
    logRelayEvent("ota_proxy_upstream_error", {
      upstream_url: upstreamUrl.toString(),
      status: upstreamResponse.status,
    });
    return jsonResponse(
      { error: "ota_upstream_error", status: upstreamResponse.status },
      upstreamResponse.status,
    );
  }

  if (upstreamPath === "/manifest.json") {
    const manifest = await upstreamResponse.json();
    return jsonResponse(rewriteOtaManifest(manifest, upstreamBase, otaProxyBaseUrl(requestUrl)));
  }

  return new Response(upstreamResponse.body, {
    status: 200,
    headers: otaProxyHeaders(upstreamResponse),
  });
}

function otaUpstreamBaseUrl(env) {
  return String(env.OTA_UPSTREAM_BASE_URL || DEFAULT_OTA_UPSTREAM_BASE_URL).replace(/\/+$/, "");
}

function otaUpstreamPath(path) {
  const relative = path.slice("/ota".length) || "/";
  if (relative === "/manifest.json" || relative === "/wifi-config.json") {
    return relative;
  }
  if (relative.startsWith("/firmware/") && relative.endsWith(".py")) {
    return relative;
  }
  return "";
}

function otaUpstreamHeaders(request, env) {
  const headers = {};
  const token = String(env.OTA_UPSTREAM_TOKEN || "");
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const accept = request.headers.get("accept");
  if (accept) {
    headers.Accept = accept;
  }
  return headers;
}

function rewriteOtaManifest(manifest, upstreamBase, proxyBase) {
  if (!isObject(manifest) || !Array.isArray(manifest.files)) {
    return manifest;
  }

  return {
    ...manifest,
    files: manifest.files.map((file) => {
      if (!isObject(file) || !file.url) {
        return file;
      }
      return {
        ...file,
        url: rewriteOtaFileUrl(file.url, upstreamBase, proxyBase),
      };
    }),
  };
}

function rewriteOtaFileUrl(value, upstreamBase, proxyBase) {
  try {
    const upstream = new URL(upstreamBase);
    const target = new URL(String(value), upstreamBase + "/");
    const upstreamPathPrefix = upstream.pathname.replace(/\/+$/, "");
    let relative = target.pathname;

    if (upstreamPathPrefix && relative.startsWith(upstreamPathPrefix + "/")) {
      relative = relative.slice(upstreamPathPrefix.length);
    }
    if (relative.startsWith("/ota/firmware/")) {
      relative = relative.slice("/ota".length);
    }
    if (!relative.startsWith("/firmware/")) {
      return String(value);
    }

    return `${proxyBase}${relative}${target.search}`;
  } catch {
    return String(value);
  }
}

function otaProxyBaseUrl(requestUrl) {
  return `${requestUrl.protocol}//${requestUrl.host}/ota`;
}

function otaProxyHeaders(upstreamResponse) {
  const headers = new Headers();
  headers.set("Cache-Control", "no-store");
  const contentType = upstreamResponse.headers.get("content-type");
  if (contentType) {
    headers.set("Content-Type", contentType);
  } else {
    headers.set("Content-Type", "application/octet-stream");
  }
  return headers;
}

async function handleZoomWebhook(request, env, ctx, deviceId = "") {
  const rawBody = await request.text();
  let body;

  try {
    body = rawBody ? JSON.parse(rawBody) : {};
  } catch {
    return jsonResponse({ error: "invalid_json" }, 400);
  }

  const event = String(body.event || "");
  const payload = isObject(body.payload) ? body.payload : {};
  const secretToken = env.ZOOM_WEBHOOK_SECRET_TOKEN || "";

  logRelayEvent("zoom_webhook_received", { zoom_event: event || "missing" });

  if (event === "endpoint.url_validation") {
    return handleUrlValidation(payload, secretToken);
  }

  if (!secretToken) {
    return jsonResponse({ error: "missing_zoom_webhook_secret" }, 500);
  }

  const verified = await verifyZoomSignature({
    rawBody,
    secretToken,
    timestamp: request.headers.get("x-zm-request-timestamp") || "",
    signature: request.headers.get("x-zm-signature") || "",
  });

  if (!verified) {
    return jsonResponse({ error: "invalid_zoom_signature" }, 401);
  }

  const assignedRoute = await handleAssignedZoomWebhook({
    body,
    event,
    payload,
    env,
    ctx,
    deviceId,
  });
  if (assignedRoute) {
    return assignedRoute;
  }

  const current = await readState(env);
  const topicFilter = zoomWebhookTopicFilter(env, deviceId);
  if (!zoomWebhookMatchesTopicFilter(payload, topicFilter)) {
    logRelayEvent("zoom_webhook_filtered", {
      zoom_event: event || "missing",
      device_id: deviceId,
      topic: String(extractMeeting(payload).topic || ""),
      filter: topicFilter,
      current: logStateFields(current),
    });
    await recordZoomWebhookHistory(env, ctx, {
      body,
      event,
      outcome: "filtered",
      currentState: current,
      responseState: current,
      metadata: zoomWebhookFilterMetadata(payload, topicFilter, false, deviceId),
    });
    return jsonResponse({ ok: true, filtered: true, state: deviceStateResponse(current, env) });
  }

  const state = stateFromZoomEvent(event, payload, env, zoomEventTimestamp(body.event_ts), current);
  if (state === null) {
    logRelayEvent("zoom_webhook_ignored", {
      zoom_event: event || "missing",
      current: logStateFields(current),
    });
    await recordZoomWebhookHistory(env, ctx, {
      body,
      event,
      outcome: "ignored",
      currentState: current,
      responseState: current,
      metadata: zoomWebhookFilterMetadata(payload, topicFilter, true, deviceId),
    });
    return jsonResponse({ ok: true, ignored: true, state: deviceStateResponse(current, env) });
  }

  if (isStaleZoomEvent(state, current)) {
    logRelayEvent("zoom_webhook_stale", {
      zoom_event: event || "missing",
      current_zoom_event_ts: Number(current.zoom_event_ts || 0),
      next_zoom_event_ts: Number(state.zoom_event_ts || 0),
      current: logStateFields(current),
      next: logStateFields(state),
    });
    await recordZoomWebhookHistory(env, ctx, {
      body,
      event,
      outcome: "stale",
      currentState: current,
      nextState: state,
      responseState: current,
      metadata: zoomWebhookFilterMetadata(payload, topicFilter, true, deviceId),
    });
    return jsonResponse({ ok: true, stale: true, state: deviceStateResponse(current, env) });
  }

  const next = await applyPostZoomEventScheduleCheck(event, state, env);

  await writeState(env, next);
  await recordZoomWebhookHistory(env, ctx, {
    body,
    event,
    outcome: "accepted",
    currentState: current,
    nextState: next,
    responseState: next,
    metadata: zoomWebhookFilterMetadata(payload, topicFilter, true, deviceId),
  });
  logStateTransition("zoom_webhook", current, next, { zoom_event: event || "missing" });
  return jsonResponse({ ok: true, state: deviceStateResponse(next, env) });
}

async function handleAssignedZoomWebhook({ body, event, payload, env, ctx, deviceId = "" }) {
  const route = zoomWebhookAssignmentRoute(env, payload, deviceId);
  if (!route.configured) {
    return null;
  }

  if (route.matches.length === 0) {
    const current = await readState(env);
    logRelayEvent("zoom_webhook_filtered", {
      zoom_event: event || "missing",
      device_id: deviceId,
      topic: String(extractMeeting(payload).topic || ""),
      filter: route.filters,
      current: logStateFields(current),
    });
    await recordZoomWebhookHistory(env, ctx, {
      body,
      event,
      outcome: "filtered",
      currentState: current,
      responseState: current,
      metadata: zoomWebhookFilterMetadata(payload, route.filters, false, deviceId, route.assignment || {}),
    });
    return jsonResponse({ ok: true, filtered: true, state: deviceStateResponse(current, env, deviceId) });
  }

  const results = [];
  for (const assignment of route.matches) {
    const current = await readState(env, assignment.device_id);
    const state = stateFromZoomEvent(event, payload, env, zoomEventTimestamp(body.event_ts), current);
    const metadata = zoomWebhookFilterMetadata(
      payload,
      [assignment.zoom_meeting_room_name],
      true,
      assignment.device_id,
      assignment,
    );

    if (state === null) {
      logRelayEvent("zoom_webhook_ignored", {
        zoom_event: event || "missing",
        device_id: assignment.device_id,
        current: logStateFields(current),
      });
      await recordZoomWebhookHistory(env, ctx, {
        body,
        event,
        outcome: "ignored",
        currentState: current,
        responseState: current,
        metadata,
      });
      results.push({ assignment, outcome: "ignored", state: current });
      continue;
    }

    if (isStaleZoomEvent(state, current)) {
      logRelayEvent("zoom_webhook_stale", {
        zoom_event: event || "missing",
        device_id: assignment.device_id,
        current_zoom_event_ts: Number(current.zoom_event_ts || 0),
        next_zoom_event_ts: Number(state.zoom_event_ts || 0),
        current: logStateFields(current),
        next: logStateFields(state),
      });
      await recordZoomWebhookHistory(env, ctx, {
        body,
        event,
        outcome: "stale",
        currentState: current,
        nextState: state,
        responseState: current,
        metadata,
      });
      results.push({ assignment, outcome: "stale", state: current });
      continue;
    }

    const next = await applyPostZoomEventScheduleCheck(event, state, env, assignment);
    await writeState(env, next, assignment.device_id);
    await recordZoomWebhookHistory(env, ctx, {
      body,
      event,
      outcome: "accepted",
      currentState: current,
      nextState: next,
      responseState: next,
      metadata,
    });
    logStateTransition("zoom_webhook", current, next, {
      zoom_event: event || "missing",
      device_id: assignment.device_id,
      physical_room_name: assignment.physical_room_name,
      zoom_meeting_room_name: assignment.zoom_meeting_room_name,
      microsoft_calendar_user_id: assignment.microsoft_calendar_user_id,
    });
    results.push({ assignment, outcome: "accepted", state: next });
  }

  const first = results[0];
  const response = {
    ok: true,
    states: results.map((result) => ({
      outcome: result.outcome,
      ...deviceStateResponse(result.state, env, result.assignment.device_id, result.assignment),
    })),
  };
  if (first) {
    response.state = deviceStateResponse(first.state, env, first.assignment.device_id, first.assignment);
  }
  if (results.every((result) => result.outcome === "ignored")) {
    response.ignored = true;
  }
  if (results.every((result) => result.outcome === "stale")) {
    response.stale = true;
  }
  return jsonResponse(response);
}

async function applyPostZoomEventScheduleCheck(event, state, env, assignment = null) {
  const calendarUserId = assignment?.microsoft_calendar_user_id || "";
  if (event !== "meeting.ended" || !hasScheduleConfig(env, calendarUserId)) {
    return state;
  }
  if (assignment && !calendarUserId) {
    return state;
  }

  try {
    const nowMs = Date.now();
    const meetings = await listScheduleMeetings(env, nowMs, calendarUserId);
    const schedule = scheduleStatusFromMeetings(meetings, state, env, nowMs);
    const next = stateFromScheduleStatus(scheduleWithoutActiveMeeting(schedule), state, env, nowMs);

    logRelayEvent("zoom_webhook_schedule_check", {
      zoom_event: event,
      device_id: assignment?.device_id || "",
      physical_room_name: assignment?.physical_room_name || "",
      microsoft_calendar_user_id: calendarUserId,
      meeting_count: meetings.length,
      schedule: publicScheduleStatus(schedule),
      current: logStateFields(state),
      next: logStateFields(next),
    });
    return next;
  } catch (error) {
    logRelayEvent("zoom_webhook_schedule_check_error", {
      zoom_event: event,
      device_id: assignment?.device_id || "",
      physical_room_name: assignment?.physical_room_name || "",
      microsoft_calendar_user_id: calendarUserId,
      error: errorMessage(error),
      state: logStateFields(state),
    });
    return state;
  }
}

function scheduleWithoutActiveMeeting(schedule) {
  return {
    ...schedule,
    active: null,
    ending: null,
  };
}

async function handleUrlValidation(payload, secretToken) {
  const plainToken = String(payload.plainToken || "");

  if (!plainToken) {
    return jsonResponse({ error: "missing_plain_token" }, 400);
  }

  if (!secretToken) {
    return jsonResponse({ error: "missing_zoom_webhook_secret" }, 500);
  }

  return jsonResponse({
    plainToken,
    encryptedToken: await hmacSha256Hex(secretToken, plainToken),
  });
}

async function handleSimulate(path, request, env) {
  if (!env.ADMIN_TOKEN) {
    return jsonResponse({ error: "simulate_disabled" }, 404);
  }

  if (!authorizeBearer(request, env.ADMIN_TOKEN)) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }

  const action = path.slice("/simulate/".length);
  const minutes = minutesFromUrl(request.url);
  const now = utcNow();
  const previous = await readState(env);
  const otaAction = action === "ota" || action === "ota-check";
  let state;

  if (action === "start") {
    state = makeStoredState({
      command: { mode: "meeting_status", state: "in_progress" },
      lastEvent: "simulate.meeting.started",
      updatedAt: now,
      source: "simulate",
    });
  } else if (action === "end" || action === "off" || action === "reset") {
    state = makeStoredState({
      command: { mode: "off" },
      lastEvent: action === "end" ? "simulate.meeting.ended" : "simulate.reset",
      updatedAt: now,
      source: "simulate",
    });
  } else if (action === "upcoming" || action === "starting-soon") {
    state = makeStoredState({
      command: { mode: "meeting_status", state: "starting_soon", minutes },
      lastEvent: "simulate.schedule.upcoming",
      updatedAt: now,
      source: "simulate",
    });
  } else if (action === "ending-soon") {
    state = makeStoredState({
      command: { mode: "meeting_status", state: "ending_soon", minutes },
      lastEvent: "simulate.schedule.ending_soon",
      updatedAt: now,
      source: "simulate",
    });
  } else if (otaAction) {
    state = {
      ...previous,
      ota_check_requested_at: now,
      updated_at: now,
      last_event: "simulate.ota.requested",
      source: "simulate",
    };
  } else {
    return jsonResponse({ error: "not_found" }, 404);
  }

  const assignments = Object.values(roomAssignments(env));
  if (assignments.length > 0) {
    const states = [];
    for (const assignment of assignments) {
      const devicePrevious = await readState(env, assignment.device_id);
      const deviceState = otaAction
        ? {
            ...devicePrevious,
            ota_check_requested_at: now,
            updated_at: now,
            last_event: "simulate.ota.requested",
            source: "simulate",
          }
        : state;
      await writeState(env, deviceState, assignment.device_id);
      logStateTransition("simulate", devicePrevious, deviceState, {
        action,
        device_id: assignment.device_id,
        physical_room_name: assignment.physical_room_name,
      });
      states.push(deviceStateResponse(deviceState, env, assignment.device_id, assignment));
    }
    return jsonResponse({ ok: true, state: states[0], states });
  }

  await writeState(env, state);
  logStateTransition("simulate", previous, state, { action });
  return jsonResponse({ ok: true, state: deviceStateResponse(state, env) });
}

function stateFromZoomEvent(event, payload, env, eventTs, currentState = {}) {
  const meeting = extractMeeting(payload);
  const now = utcNow();

  if (event === "meeting.started") {
    return makeStoredState({
      command: { mode: "meeting_status", state: "in_progress" },
      lastEvent: event,
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: true,
      activeMeeting: meeting,
      zoomState: zoomStateFromStartedMeeting(meeting, eventTs),
    });
  }

  if (event === "meeting.ended") {
    const nextMeeting = nextMeetingFromState(currentState);
    if (isStartingSoonState(currentState)) {
      return makeStoredState({
        command: normalizeCommand(currentState.command),
        lastEvent: event,
        updatedAt: now,
        meeting,
        source: "zoom",
        zoomEventTs: eventTs,
        inUse: false,
        nextMeeting,
        nextMeetingMinutes: currentState.next_meeting_minutes,
        zoomState: zoomStateFromEndedMeeting(currentState, meeting, eventTs),
      });
    }

    if (nextMeeting.id || nextMeeting.uuid) {
      return makeStoredState({
        command: {
          mode: "meeting_status",
          state: "starting_soon",
          minutes: boundedMinutes(currentState.next_meeting_minutes),
        },
        lastEvent: event,
        updatedAt: now,
        meeting,
        source: "zoom",
        zoomEventTs: eventTs,
        inUse: false,
        nextMeeting,
        nextMeetingMinutes: currentState.next_meeting_minutes,
        zoomState: zoomStateFromEndedMeeting(currentState, meeting, eventTs),
      });
    }

    return makeStoredState({
      command: { mode: "off" },
      lastEvent: event,
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: false,
      zoomState: zoomStateFromEndedMeeting(currentState, meeting, eventTs),
    });
  }

  if (event === "meeting.upcoming" || event === "schedule.upcoming") {
    return makeStoredState({
      command: {
        mode: "meeting_status",
        state: "starting_soon",
        minutes: boundedMinutes(payload.minutes || payload.minutes_until_start),
      },
      lastEvent: event,
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: false,
      nextMeeting: meeting,
      zoomState: zoomStateFromState(currentState),
    });
  }

  if (event === "meeting.ending_soon" || event === "schedule.ending_soon") {
    return makeStoredState({
      command: {
        mode: "meeting_status",
        state: "ending_soon",
        minutes: boundedMinutes(payload.minutes || payload.minutes_until_end),
      },
      lastEvent: event,
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: true,
      activeMeeting: meeting,
      zoomState: zoomStateFromState(currentState),
    });
  }

  if (env.TREAT_UNKNOWN_ZOOM_EVENTS_AS_OFF === "true") {
    return makeStoredState({
      command: { mode: "off" },
      lastEvent: event || "zoom.unknown",
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: false,
      zoomState: zoomStateFromEndedMeeting(currentState, meeting, eventTs),
    });
  }

  return null;
}

async function readState(env, deviceId = "") {
  assertKv(env);
  const stored = await env.STATE_KV.get(stateKeyForDevice(env, deviceId), "json");
  if (stored && isValidStoredState(stored)) {
    return stored;
  }

  return makeStoredState({
    command: { mode: "off" },
    lastEvent: "relay.started",
    updatedAt: utcNow(),
  });
}

async function writeState(env, state, deviceId = "") {
  assertKv(env);
  await env.STATE_KV.put(stateKeyForDevice(env, deviceId), JSON.stringify(state));
}

function stateKeyForDevice(env, deviceId = "") {
  const assignment = roomAssignmentForDevice(env, deviceId);
  if (!assignment) {
    return STATE_KEY;
  }
  return `${DEVICE_STATE_PREFIX}${assignment.device_id}`;
}

async function recordZoomWebhookHistory(env, ctx, {
  body,
  event,
  outcome,
  currentState = {},
  nextState = null,
  responseState = {},
  metadata = {},
}) {
  assertKv(env);
  const receivedAt = utcNow();
  const zoomEventTs = zoomEventTimestamp(body.event_ts);
  const payload = isObject(body.payload) ? body.payload : {};
  const record = {
    v: 1,
    received_at: receivedAt,
    event: event || "missing",
    outcome,
    zoom_event_ts: zoomEventTs,
    zoom_event_at: new Date(zoomEventTs).toISOString(),
    meeting: extractMeeting(payload),
    metadata,
    payload: body,
    current: logStateFields(currentState),
    next: nextState ? logStateFields(nextState) : null,
    state: logStateFields(responseState),
  };
  const write = env.STATE_KV.put(zoomWebhookHistoryKey(receivedAt, event), JSON.stringify(record), {
    expirationTtl: ZOOM_WEBHOOK_HISTORY_TTL_SECONDS,
  });

  if (ctx && typeof ctx.waitUntil === "function") {
    ctx.waitUntil(write.catch((error) => {
      logRelayEvent("zoom_webhook_history_error", {
        zoom_event: event || "missing",
        error: errorMessage(error),
      });
    }));
    return;
  }

  try {
    await write;
  } catch (error) {
    logRelayEvent("zoom_webhook_history_error", {
      zoom_event: event || "missing",
      error: errorMessage(error),
    });
  }
}

function zoomWebhookHistoryKey(receivedAt, event) {
  const timestamp = receivedAt.replace(/[:.]/g, "-");
  const eventName = String(event || "missing").replace(/[^a-zA-Z0-9._-]/g, "_");
  const id = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${ZOOM_WEBHOOK_HISTORY_PREFIX}${timestamp}:${eventName}:${id}`;
}

function zoomWebhookDeviceIdFromRequest(url, path) {
  if (path === "/zoom/webhook") {
    return String(url.searchParams.get("device_id") || "").trim();
  }

  const match = path.match(/^\/zoom\/([^/]+)\/webhook$/);
  if (!match) {
    return null;
  }
  return decodeURIComponent(match[1]).trim();
}

function roomAssignments(env) {
  const raw = env.PICO_ROOM_ASSIGNMENTS;
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return {};
  }

  let parsed;
  try {
    parsed = JSON.parse(String(raw));
  } catch (error) {
    logRelayEvent("pico_room_assignments_config_error", {
      error: errorMessage(error),
    });
    return {};
  }

  if (!isObject(parsed)) {
    return {};
  }

  const assignments = {};
  for (const [deviceId, value] of Object.entries(parsed)) {
    const normalizedDeviceId = String(deviceId || "").trim();
    if (!normalizedDeviceId || !isObject(value)) {
      continue;
    }
    const physicalRoomName = String(
      value.physical_room_name
      || value.PHYSICAL_ROOM_NAME
      || value.physicalRoomName
      || "",
    ).trim();
    const zoomMeetingRoomName = String(
      value.zoom_meeting_room_name
      || value.ZOOM_MEETING_ROOM_NAME
      || value.zoomMeetingRoomName
      || "",
    ).trim();
    const microsoftCalendarUserId = String(
      value.microsoft_calendar_user_id
      || value.MICROSOFT_CALENDAR_USER_ID
      || value.microsoftCalendarUserId
      || "",
    ).trim();
    if (!zoomMeetingRoomName) {
      continue;
    }
    assignments[normalizedDeviceId] = {
      device_id: normalizedDeviceId,
      physical_room_name: physicalRoomName,
      zoom_meeting_room_name: zoomMeetingRoomName,
      microsoft_calendar_user_id: microsoftCalendarUserId,
    };
  }
  return assignments;
}

function roomAssignmentForDevice(env, deviceId = "") {
  const normalizedDeviceId = String(deviceId || "").trim();
  if (!normalizedDeviceId) {
    return null;
  }
  return roomAssignments(env)[normalizedDeviceId] || null;
}

function roomAssignmentsWithCalendar(env) {
  return Object.values(roomAssignments(env)).filter((assignment) => assignment.microsoft_calendar_user_id);
}

function zoomWebhookAssignmentRoute(env, payload, deviceId = "") {
  const assignments = roomAssignments(env);
  const allAssignments = Object.values(assignments);
  if (allAssignments.length === 0) {
    return { configured: false, matches: [], filters: [] };
  }

  const normalizedDeviceId = String(deviceId || "").trim();
  if (normalizedDeviceId) {
    const assignment = assignments[normalizedDeviceId] || null;
    const filters = assignment ? [assignment.zoom_meeting_room_name] : [];
    return {
      configured: true,
      assignment,
      filters,
      matches: assignment && zoomWebhookMatchesTopicFilter(payload, filters) ? [assignment] : [],
    };
  }

  const matches = allAssignments.filter((assignment) => (
    zoomWebhookMatchesTopicFilter(payload, [assignment.zoom_meeting_room_name])
  ));
  return {
    configured: true,
    filters: allAssignments.map((assignment) => assignment.zoom_meeting_room_name),
    matches,
  };
}

function zoomWebhookTopicFilter(env, deviceId = "") {
  const deviceFilters = zoomWebhookTopicFilterMap(env);
  const normalizedDeviceId = String(deviceId || "").trim();
  if (normalizedDeviceId && Object.prototype.hasOwnProperty.call(deviceFilters, normalizedDeviceId)) {
    return topicFilterList(deviceFilters[normalizedDeviceId]);
  }
  return topicFilterList(env.ZOOM_WEBHOOK_TOPIC_FILTER);
}

function zoomWebhookTopicFilterMap(env) {
  const raw = env.ZOOM_WEBHOOK_TOPIC_FILTERS;
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return {};
  }

  try {
    const parsed = JSON.parse(String(raw));
    return isObject(parsed) ? parsed : {};
  } catch (error) {
    logRelayEvent("zoom_webhook_topic_filter_config_error", {
      error: errorMessage(error),
    });
    return {};
  }
}

function topicFilterList(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (value === undefined || value === null) {
    return [];
  }
  return String(value)
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function zoomWebhookMatchesTopicFilter(payload, filters) {
  if (filters.length === 0) {
    return true;
  }
  const topic = String(extractMeeting(payload).topic || "").toLowerCase();
  return filters.some((filter) => topic.includes(filter.toLowerCase()));
}

function zoomWebhookFilterMetadata(payload, filters, matched, deviceId = "", assignment = {}) {
  return {
    device_id: String(deviceId || ""),
    physical_room_name: String(assignment.physical_room_name || ""),
    zoom_meeting_room_name: String(assignment.zoom_meeting_room_name || ""),
    microsoft_calendar_user_id: String(assignment.microsoft_calendar_user_id || ""),
    topic_filter_configured: filters.length > 0,
    topic_filter_matched: matched,
    topic_filter: filters,
    topic: String(extractMeeting(payload).topic || ""),
  };
}

export async function runSchedulePoll(env, options = {}) {
  const checkedAt = utcNow();
  const assignments = roomAssignmentsWithCalendar(env);
  if (assignments.length > 0) {
    return runAssignedSchedulePoll(env, assignments, {
      ...options,
      checkedAt,
    });
  }

  const current = await readState(env);
  if (!hasScheduleConfig(env)) {
    logRelayEvent("schedule_poll_skipped", {
      reason: options.reason || "schedule",
      error: "missing_microsoft_calendar_config",
      current: logStateFields(current),
    });
    return {
      ok: false,
      error: "missing_microsoft_calendar_config",
      checked_at: checkedAt,
      state: deviceStateResponse(current, env),
    };
  }

  try {
    const nowMs = options.nowMs ?? Date.now();
    const meetings = await listScheduleMeetings(env, nowMs);
    const schedule = scheduleStatusFromMeetings(meetings, current, env, nowMs);
    const next = stateFromScheduleStatus(schedule, current, env, nowMs);
    const wrote = shouldWriteState(current, next);

    if (wrote) {
      await writeState(env, next);
    }

    const responseState = wrote ? next : current;
    logRelayEvent("schedule_poll", {
      reason: options.reason || "schedule",
      ok: true,
      meeting_count: meetings.length,
      wrote,
      schedule: publicScheduleStatus(schedule),
      current: logStateFields(current),
      next: logStateFields(next),
      state: logStateFields(responseState),
    });

    return {
      ok: true,
      reason: options.reason || "schedule",
      checked_at: checkedAt,
      meeting_count: meetings.length,
      schedule: publicScheduleStatus(schedule),
      state: deviceStateResponse(responseState, env),
    };
  } catch (error) {
    logRelayEvent("schedule_poll_error", {
      reason: options.reason || "schedule",
      error: errorMessage(error),
      current: logStateFields(current),
    });
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      checked_at: checkedAt,
      state: deviceStateResponse(current, env),
    };
  }
}

async function runAssignedSchedulePoll(env, assignments, options = {}) {
  const checkedAt = options.checkedAt || utcNow();
  const reason = options.reason || "schedule";
  const nowMs = options.nowMs ?? Date.now();

  if (!hasMicrosoftClientConfig(env)) {
    logRelayEvent("schedule_poll_skipped", {
      reason,
      error: "missing_microsoft_client_config",
      assignment_count: assignments.length,
    });
    return {
      ok: false,
      error: "missing_microsoft_client_config",
      checked_at: checkedAt,
      states: [],
    };
  }

  const states = [];
  const errors = [];
  for (const assignment of assignments) {
    const current = await readState(env, assignment.device_id);
    try {
      const meetings = await listScheduleMeetings(env, nowMs, assignment.microsoft_calendar_user_id);
      const schedule = scheduleStatusFromMeetings(meetings, current, env, nowMs);
      const next = stateFromScheduleStatus(schedule, current, env, nowMs);
      const wrote = shouldWriteState(current, next);

      if (wrote) {
        await writeState(env, next, assignment.device_id);
      }

      const responseState = wrote ? next : current;
      const state = {
        ok: true,
        meeting_count: meetings.length,
        wrote,
        schedule: publicScheduleStatus(schedule),
        ...deviceStateResponse(responseState, env, assignment.device_id, assignment),
      };
      states.push(state);
      logRelayEvent("schedule_poll", {
        reason,
        ok: true,
        device_id: assignment.device_id,
        physical_room_name: assignment.physical_room_name,
        microsoft_calendar_user_id: assignment.microsoft_calendar_user_id,
        meeting_count: meetings.length,
        wrote,
        schedule: publicScheduleStatus(schedule),
        current: logStateFields(current),
        next: logStateFields(next),
        state: logStateFields(responseState),
      });
    } catch (error) {
      const message = errorMessage(error);
      errors.push({
        device_id: assignment.device_id,
        physical_room_name: assignment.physical_room_name,
        microsoft_calendar_user_id: assignment.microsoft_calendar_user_id,
        error: message,
      });
      states.push({
        ok: false,
        error: message,
        ...deviceStateResponse(current, env, assignment.device_id, assignment),
      });
      logRelayEvent("schedule_poll_error", {
        reason,
        device_id: assignment.device_id,
        physical_room_name: assignment.physical_room_name,
        microsoft_calendar_user_id: assignment.microsoft_calendar_user_id,
        error: message,
        current: logStateFields(current),
      });
    }
  }

  const response = {
    ok: errors.length === 0,
    reason,
    checked_at: checkedAt,
    assignment_count: assignments.length,
    states,
  };
  if (states[0]) {
    response.state = states[0];
  }
  if (errors.length > 0) {
    response.error = "schedule_poll_partial_failure";
    response.errors = errors;
  }
  return response;
}

function scheduleStatusFromMeetings(meetings, currentState, env, nowMs = Date.now()) {
  const sortedMeetings = sortMeetings(meetings);
  const now = new Date(nowMs);
  return {
    checked_at_ms: nowMs,
    active: activeScheduledMeeting(sortedMeetings, now),
    upcoming: nextUpcomingMeeting(sortedMeetings, now, upcomingLookaheadMinutes(env, currentState)),
    ending: endingSoonMeeting(sortedMeetings, currentState, now, endingSoonMinutes(env)),
  };
}

function stateFromScheduleStatus(schedule, currentState, env = {}, nowMs = schedule?.checked_at_ms ?? Date.now()) {
  const now = utcNow();
  const activeMeeting = activeMeetingFromState(currentState);
  const zoomEventTs = Number(currentState.zoom_event_ts || 0);
  const makeScheduleState = (state) => makeStoredState({
    ...state,
    zoomState: zoomStateFromState(currentState),
  });

  if (isActiveState(currentState)) {
    const currentNextMeeting = nextMeetingFromState(currentState);
    const nextMeeting = schedule.upcoming ? schedule.upcoming.meeting : {};
    const nextMeetingMinutes = schedule.upcoming ? schedule.upcoming.minutes : null;
    const scheduledActiveMeeting = schedule.ending?.meeting || schedule.active?.meeting || activeMeeting;

    if (shouldClearAfterScheduledEnd(currentState, schedule, env, nowMs)) {
      return makeScheduleState({
        command: { mode: "off" },
        lastEvent: usesScheduledEndClearGrace(currentState) ? "schedule.end_grace_clear" : "schedule.end_clear",
        updatedAt: now,
        meeting: scheduledActiveMeeting,
        source: "schedule",
        zoomEventTs,
        inUse: false,
      });
    }

    if (schedule.ending) {
      return makeScheduleState({
        command: {
          mode: "meeting_status",
          state: "ending_soon",
          minutes: schedule.ending.minutes,
        },
        lastEvent: "schedule.ending_soon",
        updatedAt: now,
        meeting: schedule.ending.meeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting: schedule.ending.meeting,
        nextMeeting,
        nextMeetingMinutes,
      });
    }

    if (isEndingSoonState(currentState) && parseScheduleTime(currentState.active_meeting_end_at) !== null) {
      return makeScheduleState({
        command: normalizeCommand(currentState.command),
        lastEvent: currentState.last_event || "schedule.ending_soon",
        updatedAt: currentState.updated_at || now,
        meeting: scheduledActiveMeeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting: scheduledActiveMeeting,
        nextMeeting,
        nextMeetingMinutes,
      });
    }

    if (isEndingSoonState(currentState)) {
      return makeScheduleState({
        command: { mode: "meeting_status", state: "in_progress" },
        lastEvent: "schedule.end_clear",
        updatedAt: now,
        meeting: activeMeeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting,
        nextMeeting,
        nextMeetingMinutes,
      });
    }

    if (schedule.active) {
      return makeScheduleState({
        command: normalizeCommand(currentState.command),
        lastEvent: currentState.last_event || "meeting.started",
        updatedAt: currentState.updated_at || now,
        meeting: activeMeeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting: schedule.active.meeting,
        nextMeeting,
        nextMeetingMinutes,
      });
    }

    if (schedule.upcoming && !sameMeeting(currentNextMeeting, schedule.upcoming.meeting)) {
      return makeScheduleState({
        command: normalizeCommand(currentState.command),
        lastEvent: currentState.last_event || "meeting.started",
        updatedAt: now,
        meeting: activeMeeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting,
        nextMeeting: schedule.upcoming.meeting,
        nextMeetingMinutes: schedule.upcoming.minutes,
      });
    }

    if (!schedule.upcoming && (currentNextMeeting.id || currentNextMeeting.uuid)) {
      return makeScheduleState({
        command: normalizeCommand(currentState.command),
        lastEvent: currentState.last_event || "meeting.started",
        updatedAt: now,
        meeting: activeMeeting,
        source: currentState.source || "zoom",
        zoomEventTs,
        inUse: true,
        activeMeeting,
      });
    }

    return currentState;
  }

  if (schedule.active && !zoomEndedSameScheduledMeeting(currentState, schedule.active.meeting, activeMeeting)) {
    return makeScheduleState({
      command: { mode: "meeting_status", state: "in_progress" },
      lastEvent: "schedule.active",
      updatedAt: now,
      meeting: schedule.active.meeting,
      source: "schedule",
      zoomEventTs,
      inUse: true,
      activeMeeting: schedule.active.meeting,
    });
  }

  if (!schedule.upcoming && scheduleOnlyMeetingInProgress(currentState, nowMs)) {
    return currentState;
  }

  if (!schedule.upcoming && scheduleOnlyMeetingEnded(currentState, nowMs)) {
    return makeScheduleState({
      command: { mode: "off" },
      lastEvent: "schedule.end_clear",
      updatedAt: now,
      meeting: nextMeetingFromState(currentState),
      source: "schedule",
      inUse: false,
    });
  }

  if (schedule.upcoming) {
    return makeScheduleState({
      command: {
        mode: "meeting_status",
        state: "starting_soon",
        minutes: schedule.upcoming.minutes,
      },
      lastEvent: "schedule.upcoming",
      updatedAt: now,
      meeting: schedule.upcoming.meeting,
      source: "schedule",
      inUse: false,
      nextMeeting: schedule.upcoming.meeting,
      nextMeetingMinutes: schedule.upcoming.minutes,
    });
  }

  if (isScheduleDrivenState(currentState)) {
    return makeScheduleState({
      command: { mode: "off" },
      lastEvent: "schedule.clear",
      updatedAt: now,
      source: "schedule",
      inUse: false,
    });
  }

  return currentState;
}

function deviceStateResponse(state, env, deviceId = "", assignment = null) {
  const response = {
    v: 1,
    command: normalizeCommand(state.command),
    poll_seconds: pollSeconds(env),
    updated_at: String(state.updated_at || ""),
    last_event: String(state.last_event || ""),
  };
  if (state.ota_check_requested_at) {
    response.ota_check_requested_at = String(state.ota_check_requested_at);
  }
  if (deviceId) {
    response.device_id = deviceId;
  }
  if (assignment?.physical_room_name) {
    response.physical_room_name = assignment.physical_room_name;
  }
  if (assignment?.zoom_meeting_room_name) {
    response.zoom_meeting_room_name = assignment.zoom_meeting_room_name;
  }
  if (assignment?.microsoft_calendar_user_id) {
    response.microsoft_calendar_user_id = assignment.microsoft_calendar_user_id;
  }
  return response;
}

function deviceIdFromRequest(request, url, path) {
  if (path === "/device/state") {
    return String(url.searchParams.get("device_id") || request.headers.get("x-device-id") || "").trim();
  }

  const match = path.match(/^\/device\/([^/]+)\/state$/);
  if (!match) {
    return null;
  }

  return decodeURIComponent(match[1]).trim();
}

function makeStoredState({
  command,
  lastEvent,
  updatedAt,
  meeting = {},
  source = "relay",
  zoomEventTs = 0,
  inUse,
  activeMeeting = {},
  nextMeeting = {},
  nextMeetingMinutes = null,
  zoomState = {},
}) {
  const normalizedCommand = normalizeCommand(command);
  const active = isObject(activeMeeting) ? activeMeeting : {};
  const next = isObject(nextMeeting) ? nextMeeting : {};
  const zoom = normalizedZoomState(zoomState);

  return {
    v: 1,
    command: normalizedCommand,
    updated_at: updatedAt,
    last_event: lastEvent,
    source,
    zoom_event_ts: zoomEventTs,
    zoom_active: zoom.active,
    zoom_meeting_id: zoom.meeting_id,
    zoom_meeting_uuid: zoom.meeting_uuid,
    zoom_topic: zoom.topic,
    zoom_started_at: zoom.started_at,
    zoom_ended_at: zoom.ended_at,
    in_use: typeof inUse === "boolean" ? inUse : inferredInUse(normalizedCommand),
    active_meeting_id: String(active.id || active.uuid || ""),
    active_topic: String(active.topic || ""),
    active_meeting_start_at: scheduleStartAt(active),
    active_meeting_end_at: scheduleEndAt(active),
    next_meeting_id: String(next.id || next.uuid || ""),
    next_meeting_topic: String(next.topic || ""),
    next_meeting_start_at: scheduleStartAt(next),
    next_meeting_end_at: scheduleEndAt(next),
    next_meeting_minutes:
      next.id || next.uuid ? boundedMinutes(nextMeetingMinutes) : null,
    meeting: {
      id: String(meeting.id || ""),
      uuid: String(meeting.uuid || ""),
      topic: String(meeting.topic || ""),
    },
  };
}

function isStaleZoomEvent(nextState, currentState) {
  if (nextState.source !== "zoom") {
    return false;
  }

  const nextTs = Number(nextState.zoom_event_ts || 0);
  const currentTs = Number(currentState.zoom_event_ts || 0);
  return Number.isFinite(nextTs) && Number.isFinite(currentTs) && currentTs > 0 && nextTs < currentTs;
}

function nextUpcomingMeeting(meetings, now, lookaheadMinutes) {
  const lookaheadMs = lookaheadMinutes * 60 * 1000;

  for (const meeting of meetings) {
    const start = parseZoomTime(meeting.start_time);
    if (start === null) {
      continue;
    }

    const msUntilStart = start.getTime() - now.getTime();
    if (msUntilStart < 0) {
      continue;
    }
    if (msUntilStart > lookaheadMs) {
      return null;
    }

    return {
      meeting: normalizedMeeting(meeting),
      minutes: lookaheadMinutes,
      starts_at: start.toISOString(),
      ends_at: scheduleEndAt(meeting),
    };
  }

  return null;
}

function activeScheduledMeeting(meetings, now) {
  for (const meeting of meetings) {
    const window = scheduleWindow(meeting);
    if (!window) {
      continue;
    }

    const nowMs = now.getTime();
    if (window.start.getTime() <= nowMs && nowMs < window.end.getTime()) {
      return {
        meeting: normalizedMeeting(meeting),
        starts_at: window.start.toISOString(),
        ends_at: window.end.toISOString(),
      };
    }
  }

  return null;
}

function endingSoonMeeting(meetings, currentState, now, lookaheadMinutes) {
  const activeMeeting = activeMeetingFromState(currentState);
  const lookaheadMs = lookaheadMinutes * 60 * 1000;
  const shouldRequireSameMeeting = Boolean(activeMeeting.id || activeMeeting.uuid) && isScheduleDrivenState(currentState);

  for (const meeting of meetings) {
    const start = parseZoomTime(meeting.start_time);
    const durationMinutes = parsePositiveInteger(meeting.duration);
    if (start === null || durationMinutes === null) {
      continue;
    }
    if (shouldRequireSameMeeting) {
      const candidate = normalizedMeeting(meeting);
      if (!sameMeeting(activeMeeting, candidate)) {
        continue;
      }
    }

    const end = new Date(start.getTime() + durationMinutes * 60000);
    const msUntilStart = start.getTime() - now.getTime();
    const msUntilEnd = end.getTime() - now.getTime();

    if (msUntilStart > 0 || msUntilEnd < 0) {
      continue;
    }
    if (msUntilEnd > lookaheadMs) {
      return null;
    }

    return {
      meeting: normalizedMeeting(meeting),
      minutes: lookaheadMinutes,
      starts_at: start.toISOString(),
      ends_at: end.toISOString(),
    };
  }

  return null;
}

async function getMicrosoftAccessToken(env) {
  const now = Date.now();
  const tenantId = String(env.MICROSOFT_TENANT_ID || "").trim();
  const clientId = String(env.MICROSOFT_CLIENT_ID || "").trim();
  const clientSecret = String(env.MICROSOFT_CLIENT_SECRET || "").trim();
  const cacheKey = `${tenantId}:${clientId}`;

  if (microsoftTokenCache && microsoftTokenCache.cacheKey === cacheKey && microsoftTokenCache.expiresAt > now + 60000) {
    return microsoftTokenCache.accessToken;
  }

  const body = new URLSearchParams({
    grant_type: "client_credentials",
    client_id: clientId,
    client_secret: clientSecret,
    scope: "https://graph.microsoft.com/.default",
  });

  const response = await fetch(`https://login.microsoftonline.com/${encodeURIComponent(tenantId)}/oauth2/v2.0/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const json = await readJsonResponse(response, "Microsoft token");
  const accessToken = String(json.access_token || "");
  if (!accessToken) {
    throw new Error("Microsoft token response did not include an access_token");
  }

  const expiresIn = parsePositiveInteger(json.expires_in) || 3600;
  microsoftTokenCache = {
    accessToken,
    cacheKey,
    expiresAt: now + expiresIn * 1000,
  };
  return accessToken;
}

async function listScheduleMeetings(env, nowMs = Date.now(), calendarUserId = "") {
  const accessToken = await getMicrosoftAccessToken(env);
  const events = await listMicrosoftCalendarEvents(env, accessToken, nowMs, calendarUserId);
  return dedupeMeetings(events.map(graphEventToMeeting).filter(Boolean));
}

async function listMicrosoftCalendarEvents(env, accessToken, nowMs, calendarUserId = "") {
  const userId = String(calendarUserId || env.MICROSOFT_CALENDAR_USER_ID || "").trim();
  if (!userId) {
    throw new Error("missing_microsoft_calendar_user_id");
  }
  const minimumLookbackMinutes = endingSoonMinutes(env);
  const minimumLookaheadMinutes = Math.max(activeMeetingLookaheadMinutes(env), emptyRoomLookaheadMinutes(env));
  const lookbackMinutes = Math.max(
    minimumLookbackMinutes,
    positiveMinutes(env.MICROSOFT_CALENDAR_LOOKBACK_MINUTES, DEFAULT_CALENDAR_LOOKBACK_MINUTES),
  );
  const lookaheadMinutes = Math.max(
    minimumLookaheadMinutes,
    positiveMinutes(env.MICROSOFT_CALENDAR_LOOKAHEAD_MINUTES, DEFAULT_CALENDAR_LOOKAHEAD_MINUTES),
  );
  const startDateTime = new Date(nowMs - lookbackMinutes * 60000).toISOString();
  const endDateTime = new Date(nowMs + lookaheadMinutes * 60000).toISOString();
  const params = new URLSearchParams({
    startDateTime,
    endDateTime,
    "$orderby": "start/dateTime",
    "$top": "100",
  });
  const events = [];
  let url = `${MICROSOFT_GRAPH_BASE}/users/${encodeURIComponent(userId)}/calendarView?${params.toString()}`;

  while (url) {
    const json = await microsoftGetJson(url, accessToken);
    events.push(...arrayValue(json.value));
    url = typeof json["@odata.nextLink"] === "string" ? json["@odata.nextLink"] : "";
  }

  return events;
}

async function microsoftGetJson(url, accessToken) {
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      Prefer: 'outlook.timezone="UTC"',
    },
  });
  return readJsonResponse(response, "Microsoft Graph");
}

async function readJsonResponse(response, source) {
  const text = await response.text();
  const json = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const message = json.error?.message || json.error_description || json.message || json.reason || text || response.statusText;
    throw new Error(`${source} HTTP ${response.status}: ${message}`);
  }
  return json;
}

function graphEventToMeeting(event) {
  if (!isObject(event) || event.isCancelled === true) {
    return null;
  }

  const start = parseMicrosoftDateTime(event.start);
  const end = parseMicrosoftDateTime(event.end);
  if (start === null || end === null || end.getTime() <= start.getTime()) {
    return null;
  }

  return {
    id: String(event.id || ""),
    uuid: String(event.iCalUId || ""),
    topic: String(event.subject || ""),
    start_time: start.toISOString(),
    duration: Math.max(1, Math.ceil((end.getTime() - start.getTime()) / 60000)),
  };
}

function dedupeMeetings(meetings) {
  const seen = new Set();
  const deduped = [];

  for (const meeting of meetings) {
    const key = [
      meeting.uuid || "",
      meeting.id || "",
      meeting.start_time || "",
      meeting.topic || "",
    ].join("|");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(meeting);
  }

  return deduped;
}

function sortMeetings(meetings) {
  return [...meetings].sort((left, right) => {
    const leftTime = parseZoomTime(left.start_time);
    const rightTime = parseZoomTime(right.start_time);
    return (leftTime?.getTime() ?? Number.MAX_SAFE_INTEGER) - (rightTime?.getTime() ?? Number.MAX_SAFE_INTEGER);
  });
}

function publicScheduleStatus(schedule) {
  return {
    active: schedule.active
      ? { starts_at: schedule.active.starts_at, ends_at: schedule.active.ends_at }
      : null,
    upcoming: schedule.upcoming
      ? { minutes: schedule.upcoming.minutes, starts_at: schedule.upcoming.starts_at, ends_at: schedule.upcoming.ends_at }
      : null,
    ending: schedule.ending ? { minutes: schedule.ending.minutes, ends_at: schedule.ending.ends_at } : null,
  };
}

function normalizeCommand(command) {
  if (!isObject(command)) {
    return { mode: "off" };
  }

  if (command.mode === "meeting_status") {
    if (command.state === "in_progress") {
      return { mode: "meeting_status", state: "in_progress" };
    }

    if (command.state === "starting_soon" || command.state === "ending_soon") {
      return {
        mode: "meeting_status",
        state: command.state,
        minutes: boundedMinutes(command.minutes),
      };
    }
  }

  return { mode: "off" };
}

function shouldWriteState(currentState, nextState) {
  const currentCommand = normalizeCommand(currentState.command);
  const nextCommand = normalizeCommand(nextState.command);

  return (
    JSON.stringify(currentCommand) !== JSON.stringify(nextCommand) ||
    String(currentState.last_event || "") !== String(nextState.last_event || "") ||
    String(currentState.active_meeting_id || "") !== String(nextState.active_meeting_id || "") ||
    String(currentState.active_meeting_start_at || "") !== String(nextState.active_meeting_start_at || "") ||
    String(currentState.active_meeting_end_at || "") !== String(nextState.active_meeting_end_at || "") ||
    String(currentState.next_meeting_id || "") !== String(nextState.next_meeting_id || "") ||
    String(currentState.next_meeting_start_at || "") !== String(nextState.next_meeting_start_at || "") ||
    String(currentState.next_meeting_end_at || "") !== String(nextState.next_meeting_end_at || "") ||
    String(currentState.next_meeting_minutes ?? "") !== String(nextState.next_meeting_minutes ?? "") ||
    String(currentState.zoom_active ?? "") !== String(nextState.zoom_active ?? "") ||
    String(currentState.zoom_meeting_id || "") !== String(nextState.zoom_meeting_id || "") ||
    String(currentState.zoom_meeting_uuid || "") !== String(nextState.zoom_meeting_uuid || "") ||
    String(currentState.zoom_topic || "") !== String(nextState.zoom_topic || "") ||
    String(currentState.zoom_started_at || "") !== String(nextState.zoom_started_at || "") ||
    String(currentState.zoom_ended_at || "") !== String(nextState.zoom_ended_at || "")
  );
}

function hasScheduleConfig(env, calendarUserId = "") {
  return Boolean(
    hasMicrosoftClientConfig(env) &&
      (calendarUserId || env.MICROSOFT_CALENDAR_USER_ID),
  );
}

function hasMicrosoftClientConfig(env) {
  return Boolean(
    env.MICROSOFT_TENANT_ID &&
      env.MICROSOFT_CLIENT_ID &&
      env.MICROSOFT_CLIENT_SECRET,
  );
}

function upcomingLookaheadMinutes(env, currentState) {
  if (isActiveState(currentState)) {
    return activeMeetingLookaheadMinutes(env);
  }
  return emptyRoomLookaheadMinutes(env);
}

function activeMeetingLookaheadMinutes(env) {
  return positiveMinutes(
    env.ACTIVE_MEETING_LOOKAHEAD_MINUTES ?? env.SCHEDULE_LOOKAHEAD_MINUTES,
    DEFAULT_ACTIVE_MEETING_LOOKAHEAD_MINUTES,
  );
}

function emptyRoomLookaheadMinutes(env) {
  return positiveMinutes(
    env.EMPTY_ROOM_LOOKAHEAD_MINUTES ?? env.SCHEDULE_LOOKAHEAD_MINUTES,
    DEFAULT_EMPTY_ROOM_LOOKAHEAD_MINUTES,
  );
}

function endingSoonMinutes(env) {
  return positiveMinutes(env.ENDING_SOON_MINUTES, DEFAULT_ENDING_SOON_MINUTES);
}

function scheduleEndClearGraceMinutes(env) {
  return positiveMinutes(
    env.SCHEDULE_END_CLEAR_GRACE_MINUTES,
    DEFAULT_SCHEDULE_END_CLEAR_GRACE_MINUTES,
  );
}

function shouldClearAfterScheduledEnd(currentState, schedule, env, nowMs) {
  const end = parseScheduleTime(
    schedule.ending?.ends_at ||
      schedule.active?.ends_at ||
      currentState.active_meeting_end_at,
  );
  if (end === null) {
    return false;
  }
  return nowMs >= end.getTime() + scheduledEndClearGraceMs(currentState, env);
}

function scheduledEndClearGraceMs(currentState, env) {
  return usesScheduledEndClearGrace(currentState) ? scheduleEndClearGraceMinutes(env) * 60000 : 0;
}

function usesScheduledEndClearGrace(currentState) {
  if (typeof currentState.zoom_active === "boolean") {
    return currentState.zoom_active;
  }
  return String(currentState.source || "") === "zoom" && isActiveState(currentState);
}

function scheduleOnlyMeetingInProgress(currentState, nowMs) {
  if (!isScheduleDrivenState(currentState)) {
    return false;
  }
  const end = parseScheduleTime(currentState.next_meeting_end_at);
  return end !== null && nowMs < end.getTime();
}

function scheduleOnlyMeetingEnded(currentState, nowMs) {
  if (!isScheduleDrivenState(currentState)) {
    return false;
  }
  const end = parseScheduleTime(currentState.next_meeting_end_at);
  return end !== null && nowMs >= end.getTime();
}

function zoomEndedSameScheduledMeeting(currentState, scheduledMeeting, currentMeeting = activeMeetingFromState(currentState)) {
  return (
    String(currentState.source || "") === "zoom" &&
    String(currentState.last_event || "") === "meeting.ended" &&
    sameMeeting(currentMeeting, scheduledMeeting)
  );
}

function positiveMinutes(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.max(1, Math.min(240, parsed));
}

function isActiveState(state) {
  if (typeof state.in_use === "boolean") {
    return state.in_use;
  }

  const command = normalizeCommand(state.command);
  return command.mode === "meeting_status" && (command.state === "in_progress" || command.state === "ending_soon");
}

function isStartingSoonState(state) {
  const command = normalizeCommand(state.command);
  return command.mode === "meeting_status" && command.state === "starting_soon";
}

function isEndingSoonState(state) {
  const command = normalizeCommand(state.command);
  return command.mode === "meeting_status" && command.state === "ending_soon";
}

function isScheduleDrivenState(state) {
  return String(state.source || "") === "schedule" || String(state.last_event || "").startsWith("schedule.");
}

function inferredInUse(command) {
  return command.mode === "meeting_status" && (command.state === "in_progress" || command.state === "ending_soon");
}

function normalizedZoomState(state = {}) {
  return {
    active: state.active === true,
    meeting_id: String(state.meeting_id || ""),
    meeting_uuid: String(state.meeting_uuid || ""),
    topic: String(state.topic || ""),
    started_at: String(state.started_at || ""),
    ended_at: String(state.ended_at || ""),
  };
}

function zoomStateFromState(state = {}) {
  return normalizedZoomState({
    active: state.zoom_active === true,
    meeting_id: state.zoom_meeting_id,
    meeting_uuid: state.zoom_meeting_uuid,
    topic: state.zoom_topic,
    started_at: state.zoom_started_at,
    ended_at: state.zoom_ended_at,
  });
}

function zoomStateFromStartedMeeting(meeting, eventTs) {
  const normalized = normalizedMeeting(meeting);
  return normalizedZoomState({
    active: true,
    meeting_id: normalized.id,
    meeting_uuid: normalized.uuid,
    topic: normalized.topic,
    started_at: zoomEventTimeIso(eventTs),
    ended_at: "",
  });
}

function zoomStateFromEndedMeeting(currentState, meeting, eventTs) {
  const currentZoom = zoomStateFromState(currentState);
  const normalized = normalizedMeeting(meeting);
  return normalizedZoomState({
    active: false,
    meeting_id: normalized.id || currentZoom.meeting_id,
    meeting_uuid: normalized.uuid || currentZoom.meeting_uuid,
    topic: normalized.topic || currentZoom.topic,
    started_at: currentZoom.started_at,
    ended_at: zoomEventTimeIso(eventTs),
  });
}

function zoomEventTimeIso(eventTs) {
  const parsed = Number(eventTs);
  if (Number.isFinite(parsed) && parsed > 0) {
    return new Date(parsed).toISOString();
  }
  return utcNow();
}

function activeMeetingFromState(state) {
  const meeting = isObject(state.meeting) ? state.meeting : {};
  return normalizedMeeting({
    id: state.active_meeting_id || meeting.id,
    uuid: meeting.uuid,
    topic: state.active_topic || meeting.topic,
    start_at: state.active_meeting_start_at,
    end_at: state.active_meeting_end_at,
  });
}

function nextMeetingFromState(state) {
  return normalizedMeeting({
    id: state.next_meeting_id,
    topic: state.next_meeting_topic,
    start_at: state.next_meeting_start_at,
    end_at: state.next_meeting_end_at,
  });
}

function normalizedMeeting(meeting) {
  return {
    id: String(meeting?.id || ""),
    uuid: String(meeting?.uuid || ""),
    topic: String(meeting?.topic || ""),
    start_at: scheduleStartAt(meeting),
    end_at: scheduleEndAt(meeting),
  };
}

function scheduleWindow(meeting) {
  const start = parseScheduleTime(meeting?.start_at || meeting?.start_time);
  if (start === null) {
    return null;
  }

  const explicitEnd = parseScheduleTime(meeting?.end_at || meeting?.end_time);
  if (explicitEnd !== null && explicitEnd.getTime() > start.getTime()) {
    return { start, end: explicitEnd };
  }

  const durationMinutes = parsePositiveInteger(meeting?.duration);
  if (durationMinutes === null) {
    return null;
  }

  return {
    start,
    end: new Date(start.getTime() + durationMinutes * 60000),
  };
}

function scheduleStartAt(meeting) {
  const value = isObject(meeting) ? meeting.start_at || meeting.start_time : meeting;
  const start = parseScheduleTime(value);
  return start === null ? "" : start.toISOString();
}

function scheduleEndAt(meeting) {
  if (!isObject(meeting)) {
    const end = parseScheduleTime(meeting);
    return end === null ? "" : end.toISOString();
  }

  const window = scheduleWindow(meeting);
  return window === null ? "" : window.end.toISOString();
}

function sameMeeting(left, right) {
  return Boolean(
    (left.id && right.id && left.id === right.id) ||
      (left.uuid && right.uuid && left.uuid === right.uuid),
  );
}

function parseZoomTime(value) {
  return parseScheduleTime(value);
}

function parseScheduleTime(value) {
  if (!value) {
    return null;
  }

  const date = new Date(String(value).replace("Z", "+00:00"));
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date;
}

function parseMicrosoftDateTime(value) {
  if (!isObject(value) || !value.dateTime) {
    return null;
  }

  const raw = String(value.dateTime);
  const hasOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(raw);
  const date = new Date(hasOffset ? raw : `${raw}Z`);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date;
}

function parsePositiveInteger(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function arrayValue(value) {
  return Array.isArray(value) ? value : [];
}

function isValidStoredState(value) {
  return isObject(value) && value.v === 1 && isObject(value.command);
}

function extractMeeting(payload) {
  const meeting = isObject(payload.object) ? payload.object : payload;
  return {
    id: meeting.id,
    uuid: meeting.uuid,
    topic: meeting.topic,
  };
}

async function verifyZoomSignature({ rawBody, secretToken, timestamp, signature }) {
  if (!timestamp || !signature) {
    return false;
  }

  const requestSeconds = Number.parseInt(timestamp, 10);
  if (!Number.isFinite(requestSeconds)) {
    return false;
  }

  const nowSeconds = Math.floor(Date.now() / 1000);
  if (Math.abs(nowSeconds - requestSeconds) > SIGNATURE_TOLERANCE_SECONDS) {
    return false;
  }

  const message = `v0:${timestamp}:${rawBody}`;
  const expected = `v0=${await hmacSha256Hex(secretToken, message)}`;
  return constantTimeEqual(expected, signature);
}

async function hmacSha256Hex(secret, message) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function authorizeBearer(request, token) {
  if (!token) {
    return true;
  }
  return request.headers.get("Authorization") === `Bearer ${token}`;
}

function devicePollToken(env) {
  return String(env.STATE_POLL_TOKEN || env.DEVICE_POLL_TOKEN || env.DEVICE_TOKEN || "");
}

function pollSeconds(env) {
  const parsed = Number.parseInt(env.POLL_SECONDS || "", 10);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_POLL_SECONDS;
  }
  return Math.max(1, Math.min(300, parsed));
}

function boundedMinutes(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return 5;
  }
  return Math.max(0, Math.min(120, parsed));
}

function minutesFromUrl(rawUrl) {
  const value = new URL(rawUrl).searchParams.get("minutes");
  return boundedMinutes(value);
}

function zoomEventTimestamp(value) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isFinite(parsed) && parsed > 0) {
    return parsed;
  }
  return Date.now();
}

function logStateTransition(reason, currentState, nextState, fields = {}) {
  logRelayEvent("state_transition", {
    reason,
    ...fields,
    previous: logStateFields(currentState),
    next: logStateFields(nextState),
  });
}

function logRelayEvent(event, fields = {}) {
  try {
    console.log(JSON.stringify({ event, ...fields }));
  } catch (error) {
    console.log(JSON.stringify({ event, log_error: errorMessage(error) }));
  }
}

function logStateFields(state = {}) {
  const command = normalizeCommand(state.command);
  return {
    command,
    command_state: command.mode === "meeting_status" ? command.state : command.mode,
    last_event: String(state.last_event || ""),
    source: String(state.source || ""),
    updated_at: String(state.updated_at || ""),
    age_seconds: stateAgeSeconds(state),
    in_use: isActiveState(state),
    zoom_active: state.zoom_active === true,
    zoom_meeting_id: String(state.zoom_meeting_id || ""),
    zoom_started_at: String(state.zoom_started_at || ""),
    zoom_ended_at: String(state.zoom_ended_at || ""),
    active_meeting_id: String(state.active_meeting_id || ""),
    active_meeting_start_at: String(state.active_meeting_start_at || ""),
    active_meeting_end_at: String(state.active_meeting_end_at || ""),
    next_meeting_id: String(state.next_meeting_id || ""),
    next_meeting_start_at: String(state.next_meeting_start_at || ""),
    next_meeting_end_at: String(state.next_meeting_end_at || ""),
  };
}

function stateAgeSeconds(state) {
  const updated = Date.parse(String(state.updated_at || ""));
  if (!Number.isFinite(updated)) {
    return null;
  }
  return Math.max(0, Math.floor((Date.now() - updated) / 1000));
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: JSON_HEADERS,
  });
}

function stripTrailingSlash(path) {
  if (path.length > 1 && path.endsWith("/")) {
    return path.slice(0, -1);
  }
  return path;
}

function assertKv(env) {
  if (!env.STATE_KV) {
    throw new Error("STATE_KV binding is not configured");
  }
}

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function utcNow() {
  return new Date().toISOString();
}

function constantTimeEqual(left, right) {
  if (left.length !== right.length) {
    return false;
  }

  let diff = 0;
  for (let index = 0; index < left.length; index += 1) {
    diff |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return diff === 0;
}

export const testInternals = {
  graphEventToMeeting,
  hmacSha256Hex,
  listScheduleMeetings,
  scheduleStatusFromMeetings,
  stateFromScheduleStatus,
  verifyZoomSignature,
};
