const STATE_KEY = "current-state";
const DEFAULT_POLL_SECONDS = 5;
const SIGNATURE_TOLERANCE_SECONDS = 300;
const DEFAULT_SCHEDULE_LOOKAHEAD_MINUTES = 5;
const DEFAULT_ENDING_SOON_MINUTES = 5;
const ZOOM_API_BASE = "https://api.zoom.us/v2";
const ZOOM_TOKEN_URL = "https://zoom.us/oauth/token";

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

let zoomTokenCache = null;

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      console.error(error);
      return jsonResponse({ error: "internal_error" }, 500);
    }
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runSchedulePoll(env, { reason: `cron:${controller.cron}` }));
  },
};

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const path = stripTrailingSlash(url.pathname);

  if (request.method === "GET" && path === "/health") {
    return jsonResponse({ ok: true });
  }

  if (request.method === "GET" && path === "/device/state") {
    if (!authorizeBearer(request, env.DEVICE_TOKEN)) {
      return jsonResponse({ error: "unauthorized" }, 401);
    }

    const state = await readState(env);
    return jsonResponse(deviceStateResponse(state, env));
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

  if (request.method === "POST" && path === "/zoom/webhook") {
    return handleZoomWebhook(request, env);
  }

  if ((request.method === "GET" || request.method === "POST") && path.startsWith("/simulate/")) {
    return handleSimulate(path, request, env);
  }

  return jsonResponse({ error: "not_found" }, 404);
}

async function handleZoomWebhook(request, env) {
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

  console.log(JSON.stringify({ route: "/zoom/webhook", event: event || "missing" }));

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

  const current = await readState(env);
  const state = stateFromZoomEvent(event, payload, env, zoomEventTimestamp(body.event_ts));
  if (state === null) {
    return jsonResponse({ ok: true, ignored: true, state: deviceStateResponse(current, env) });
  }

  if (isStaleZoomEvent(state, current)) {
    return jsonResponse({ ok: true, stale: true, state: deviceStateResponse(current, env) });
  }

  await writeState(env, state);
  return jsonResponse({ ok: true, state: deviceStateResponse(state, env) });
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
  } else {
    return jsonResponse({ error: "not_found" }, 404);
  }

  await writeState(env, state);
  return jsonResponse({ ok: true, state: deviceStateResponse(state, env) });
}

function stateFromZoomEvent(event, payload, env, eventTs) {
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
    });
  }

  if (event === "meeting.ended") {
    return makeStoredState({
      command: { mode: "off" },
      lastEvent: event,
      updatedAt: now,
      meeting,
      source: "zoom",
      zoomEventTs: eventTs,
      inUse: false,
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
    });
  }

  return null;
}

async function readState(env) {
  assertKv(env);
  const stored = await env.STATE_KV.get(STATE_KEY, "json");
  if (stored && isValidStoredState(stored)) {
    return stored;
  }

  return makeStoredState({
    command: { mode: "off" },
    lastEvent: "relay.started",
    updatedAt: utcNow(),
  });
}

async function writeState(env, state) {
  assertKv(env);
  await env.STATE_KV.put(STATE_KEY, JSON.stringify(state));
}

export async function runSchedulePoll(env, options = {}) {
  const checkedAt = utcNow();
  const current = await readState(env);

  if (!hasZoomScheduleConfig(env)) {
    return {
      ok: false,
      error: "missing_zoom_schedule_config",
      checked_at: checkedAt,
      state: deviceStateResponse(current, env),
    };
  }

  try {
    const accessToken = await getZoomAccessToken(env);
    const meetings = await listZoomScheduleMeetings(env, accessToken);
    const schedule = scheduleStatusFromMeetings(meetings, current, env, options.nowMs ?? Date.now());
    const next = stateFromScheduleStatus(schedule, current);

    if (shouldWriteState(current, next)) {
      await writeState(env, next);
    }

    return {
      ok: true,
      reason: options.reason || "schedule",
      checked_at: checkedAt,
      meeting_count: meetings.length,
      schedule: publicScheduleStatus(schedule),
      state: deviceStateResponse(shouldWriteState(current, next) ? next : current, env),
    };
  } catch (error) {
    console.error(error);
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      checked_at: checkedAt,
      state: deviceStateResponse(current, env),
    };
  }
}

function scheduleStatusFromMeetings(meetings, currentState, env, nowMs = Date.now()) {
  const sortedMeetings = sortMeetings(meetings);
  const now = new Date(nowMs);
  return {
    upcoming: nextUpcomingMeeting(sortedMeetings, now, scheduleLookaheadMinutes(env)),
    ending: endingSoonMeeting(sortedMeetings, currentState, now, endingSoonMinutes(env)),
  };
}

function stateFromScheduleStatus(schedule, currentState) {
  const now = utcNow();
  const activeMeeting = activeMeetingFromState(currentState);
  const zoomEventTs = Number(currentState.zoom_event_ts || 0);

  if (isActiveState(currentState)) {
    if (schedule.ending) {
      return makeStoredState({
        command: {
          mode: "meeting_status",
          state: "ending_soon",
          minutes: schedule.ending.minutes,
        },
        lastEvent: "schedule.ending_soon",
        updatedAt: now,
        meeting: schedule.ending.meeting,
        source: "schedule",
        zoomEventTs,
        inUse: true,
        activeMeeting: schedule.ending.meeting,
      });
    }

    return makeStoredState({
      command: { mode: "meeting_status", state: "in_progress" },
      lastEvent: "schedule.active",
      updatedAt: now,
      meeting: activeMeeting,
      source: "schedule",
      zoomEventTs,
      inUse: true,
      activeMeeting,
    });
  }

  if (schedule.upcoming) {
    return makeStoredState({
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
    });
  }

  if (isScheduleDrivenState(currentState)) {
    return makeStoredState({
      command: { mode: "off" },
      lastEvent: "schedule.clear",
      updatedAt: now,
      source: "schedule",
      inUse: false,
    });
  }

  return currentState;
}

function deviceStateResponse(state, env) {
  return {
    v: 1,
    command: normalizeCommand(state.command),
    poll_seconds: pollSeconds(env),
    updated_at: String(state.updated_at || ""),
    last_event: String(state.last_event || ""),
  };
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
}) {
  const normalizedCommand = normalizeCommand(command);
  const active = isObject(activeMeeting) ? activeMeeting : {};
  const next = isObject(nextMeeting) ? nextMeeting : {};

  return {
    v: 1,
    command: normalizedCommand,
    updated_at: updatedAt,
    last_event: lastEvent,
    source,
    zoom_event_ts: zoomEventTs,
    in_use: typeof inUse === "boolean" ? inUse : inferredInUse(normalizedCommand),
    active_meeting_id: String(active.id || active.uuid || ""),
    active_topic: String(active.topic || ""),
    next_meeting_id: String(next.id || next.uuid || ""),
    next_meeting_topic: String(next.topic || ""),
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
    };
  }

  return null;
}

function endingSoonMeeting(meetings, currentState, now, lookaheadMinutes) {
  const activeMeeting = activeMeetingFromState(currentState);
  const lookaheadMs = lookaheadMinutes * 60 * 1000;

  for (const meeting of meetings) {
    const start = parseZoomTime(meeting.start_time);
    const durationMinutes = parsePositiveInteger(meeting.duration);
    if (start === null || durationMinutes === null) {
      continue;
    }
    if (activeMeeting.id || activeMeeting.uuid) {
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
      ends_at: end.toISOString(),
    };
  }

  return null;
}

async function getZoomAccessToken(env) {
  const now = Date.now();
  if (zoomTokenCache && zoomTokenCache.expiresAt > now + 60000) {
    return zoomTokenCache.accessToken;
  }

  const body = new URLSearchParams({
    grant_type: "account_credentials",
    account_id: env.ZOOM_ACCOUNT_ID,
  });
  const credentials = btoa(`${env.ZOOM_CLIENT_ID}:${env.ZOOM_CLIENT_SECRET}`);
  const response = await fetch(ZOOM_TOKEN_URL, {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const json = await readZoomJson(response);
  const accessToken = String(json.access_token || "");
  if (!accessToken) {
    throw new Error("Zoom token response did not include an access_token");
  }

  const expiresIn = parsePositiveInteger(json.expires_in) || 3600;
  zoomTokenCache = {
    accessToken,
    expiresAt: now + expiresIn * 1000,
  };
  return accessToken;
}

async function listZoomScheduleMeetings(env, accessToken) {
  const userId = env.ZOOM_SCHEDULE_USER_ID || env.ZOOM_USER_ID || "me";
  const results = [];
  const errors = [];

  for (const loader of [listZoomUserMeetings, listZoomUpcomingMeetings]) {
    try {
      results.push(...(await loader(userId, accessToken)));
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error));
    }
  }

  if (!results.length && errors.length === 2) {
    throw new Error(`Zoom schedule polling failed: ${errors.join("; ")}`);
  }

  return dedupeMeetings(results);
}

async function listZoomUserMeetings(userId, accessToken) {
  const meetings = [];
  let nextPageToken = "";

  do {
    const params = new URLSearchParams({
      type: "upcoming",
      page_size: "300",
    });
    if (nextPageToken) {
      params.set("next_page_token", nextPageToken);
    }

    const json = await zoomGetJson(
      `/users/${encodeURIComponent(userId)}/meetings?${params.toString()}`,
      accessToken,
    );
    meetings.push(...arrayValue(json.meetings));
    nextPageToken = String(json.next_page_token || "");
  } while (nextPageToken);

  return meetings;
}

async function listZoomUpcomingMeetings(userId, accessToken) {
  const meetings = [];
  let nextPageToken = "";

  do {
    const params = new URLSearchParams({ page_size: "300" });
    if (nextPageToken) {
      params.set("next_page_token", nextPageToken);
    }

    const json = await zoomGetJson(
      `/users/${encodeURIComponent(userId)}/upcoming_meetings?${params.toString()}`,
      accessToken,
    );
    meetings.push(...arrayValue(json.meetings));
    nextPageToken = String(json.next_page_token || "");
  } while (nextPageToken);

  return meetings;
}

async function zoomGetJson(path, accessToken) {
  const response = await fetch(`${ZOOM_API_BASE}${path}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  return readZoomJson(response);
}

async function readZoomJson(response) {
  const text = await response.text();
  const json = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const message = json.message || json.reason || text || response.statusText;
    throw new Error(`Zoom API HTTP ${response.status}: ${message}`);
  }
  return json;
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
    upcoming: schedule.upcoming
      ? { minutes: schedule.upcoming.minutes, starts_at: schedule.upcoming.starts_at }
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
    String(currentState.next_meeting_id || "") !== String(nextState.next_meeting_id || "")
  );
}

function hasZoomScheduleConfig(env) {
  return Boolean(
    env.ZOOM_ACCOUNT_ID &&
      env.ZOOM_CLIENT_ID &&
      env.ZOOM_CLIENT_SECRET &&
      (env.ZOOM_SCHEDULE_USER_ID || env.ZOOM_USER_ID),
  );
}

function scheduleLookaheadMinutes(env) {
  return positiveMinutes(env.SCHEDULE_LOOKAHEAD_MINUTES, DEFAULT_SCHEDULE_LOOKAHEAD_MINUTES);
}

function endingSoonMinutes(env) {
  return positiveMinutes(env.ENDING_SOON_MINUTES, DEFAULT_ENDING_SOON_MINUTES);
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

function isScheduleDrivenState(state) {
  return String(state.source || "") === "schedule" || String(state.last_event || "").startsWith("schedule.");
}

function inferredInUse(command) {
  return command.mode === "meeting_status" && (command.state === "in_progress" || command.state === "ending_soon");
}

function activeMeetingFromState(state) {
  const meeting = isObject(state.meeting) ? state.meeting : {};
  return normalizedMeeting({
    id: state.active_meeting_id || meeting.id,
    uuid: meeting.uuid,
    topic: state.active_topic || meeting.topic,
  });
}

function normalizedMeeting(meeting) {
  return {
    id: String(meeting?.id || ""),
    uuid: String(meeting?.uuid || ""),
    topic: String(meeting?.topic || ""),
  };
}

function sameMeeting(left, right) {
  return Boolean(
    (left.id && right.id && left.id === right.id) ||
      (left.uuid && right.uuid && left.uuid === right.uuid),
  );
}

function parseZoomTime(value) {
  if (!value) {
    return null;
  }

  const date = new Date(String(value).replace("Z", "+00:00"));
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
  hmacSha256Hex,
  scheduleStatusFromMeetings,
  stateFromScheduleStatus,
  verifyZoomSignature,
};
