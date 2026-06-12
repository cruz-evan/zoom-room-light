import assert from "node:assert/strict";
import { describe, it } from "node:test";

import worker, { testInternals } from "../src/worker.js";

class MemoryKv {
  constructor() {
    this.values = new Map();
    this.options = new Map();
  }

  async get(key, type) {
    const value = this.values.get(key);
    if (value === undefined) {
      return null;
    }
    if (type === "json") {
      return JSON.parse(value);
    }
    return value;
  }

  async put(key, value, options = {}) {
    this.values.set(key, value);
    this.options.set(key, options);
  }
}

function env(overrides = {}) {
  return {
    STATE_KV: new MemoryKv(),
    ZOOM_WEBHOOK_SECRET_TOKEN: "zoom-secret",
    DEVICE_TOKEN: "",
    ADMIN_TOKEN: "admin-token",
    POLL_SECONDS: "5",
    ...overrides,
  };
}

async function json(response) {
  return response.json();
}

async function signedZoomRequest(url, body, secret = "zoom-secret") {
  const rawBody = JSON.stringify(body);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = `v0=${await testInternals.hmacSha256Hex(secret, `v0:${timestamp}:${rawBody}`)}`;

  return new Request(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-zm-request-timestamp": timestamp,
      "x-zm-signature": signature,
    },
    body: rawBody,
  });
}

describe("Cloudflare Worker relay", () => {
  it("returns the default Pico state", async () => {
    const response = await worker.fetch(new Request("https://relay.test/device/state"), env());
    assert.equal(response.status, 200);
    const body = await json(response);
    assert.match(body.updated_at, /^\d{4}-\d{2}-\d{2}T/);
    assert.deepEqual(body, {
      v: 1,
      command: { mode: "off" },
      poll_seconds: 5,
      updated_at: body.updated_at,
      last_event: "relay.started",
    });
  });

  it("requires the optional device token when configured", async () => {
    const secureEnv = env({ DEVICE_TOKEN: "device-token" });
    const unauthorized = await worker.fetch(new Request("https://relay.test/device/state"), secureEnv);
    assert.equal(unauthorized.status, 401);

    const authorized = await worker.fetch(
      new Request("https://relay.test/device/state", {
        headers: { Authorization: "Bearer device-token" },
      }),
      secureEnv,
    );
    assert.equal(authorized.status, 200);
  });

  it("accepts device_id query metadata without changing old state behavior", async () => {
    const response = await worker.fetch(new Request("https://relay.test/device/state?device_id=board-room-a"), env());
    assert.equal(response.status, 200);
    const body = await json(response);
    assert.equal(body.device_id, "board-room-a");
    assert.deepEqual(body.command, { mode: "off" });
  });

  it("accepts device id path metadata for future multi-room routing", async () => {
    const response = await worker.fetch(new Request("https://relay.test/device/board-room-b/state"), env());
    assert.equal(response.status, 200);
    const body = await json(response);
    assert.equal(body.device_id, "board-room-b");
    assert.deepEqual(body.command, { mode: "off" });
  });

  it("answers Zoom url validation with the expected encrypted token", async () => {
    const response = await worker.fetch(
      new Request("https://relay.test/zoom/webhook", {
        method: "POST",
        body: JSON.stringify({
          event: "endpoint.url_validation",
          payload: { plainToken: "plain-token" },
        }),
      }),
      env(),
    );

    assert.equal(response.status, 200);
    assert.deepEqual(await json(response), {
      plainToken: "plain-token",
      encryptedToken: await testInternals.hmacSha256Hex("zoom-secret", "plain-token"),
    });
  });

  it("rejects unsigned Zoom events", async () => {
    const relayEnv = env();
    const response = await worker.fetch(
      new Request("https://relay.test/zoom/webhook", {
        method: "POST",
        body: JSON.stringify({ event: "meeting.started", payload: {} }),
      }),
      relayEnv,
    );
    assert.equal(response.status, 401);
    assert.equal([...relayEnv.STATE_KV.values.keys()].some((key) => key.startsWith("zoom-webhook:")), false);
  });

  it("stores meeting.started as in_progress", async () => {
    const relayEnv = env();
    const response = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        payload: { object: { id: "123", topic: "Demo" } },
      }),
      relayEnv,
    );
    assert.equal(response.status, 200);

    const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
    assert.deepEqual(state.command, { mode: "meeting_status", state: "in_progress" });
    assert.equal(state.last_event, "meeting.started");
  });

  it("keeps a seven-day KV history record for signed Zoom webhooks", async () => {
    const relayEnv = env();
    const response = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        event_ts: 1781121912579,
        payload: { object: { id: "123", uuid: "uuid-123", topic: "Demo" } },
      }),
      relayEnv,
    );
    assert.equal(response.status, 200);

    const historyKeys = [...relayEnv.STATE_KV.values.keys()].filter((key) => key.startsWith("zoom-webhook:"));
    assert.equal(historyKeys.length, 1);
    assert.match(historyKeys[0], /^zoom-webhook:/);
    assert.deepEqual(relayEnv.STATE_KV.options.get(historyKeys[0]), { expirationTtl: 604800 });

    const record = JSON.parse(relayEnv.STATE_KV.values.get(historyKeys[0]));
    assert.equal(record.event, "meeting.started");
    assert.equal(record.outcome, "accepted");
    assert.equal(record.zoom_event_ts, 1781121912579);
    assert.equal(record.zoom_event_at, "2026-06-10T20:05:12.579Z");
    assert.equal(record.payload.payload.object.topic, "Demo");
    assert.equal(record.state.source, "zoom");
    assert.equal(record.state.active_meeting_id, "123");
  });

  it("applies meeting.ended webhooks even when they do not match the active meeting", async () => {
    const relayEnv = env();
    await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        payload: { object: { id: "active", topic: "Room meeting" } },
      }),
      relayEnv,
    );

    const response = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.ended",
        payload: { object: { id: "other", topic: "Org meeting" } },
      }),
      relayEnv,
    );
    const responseBody = await json(response);

    assert.equal(response.status, 200);
    assert.equal(responseBody.ignored, undefined);

    const historyRecords = [...relayEnv.STATE_KV.values.entries()]
      .filter(([key]) => key.startsWith("zoom-webhook:"))
      .map(([, value]) => JSON.parse(value));
    const acceptedEnd = historyRecords.find((record) => record.event === "meeting.ended");
    assert.equal(acceptedEnd.outcome, "accepted");
    assert.equal(acceptedEnd.meeting.id, "other");
    assert.equal(acceptedEnd.meeting.topic, "Org meeting");
    assert.equal(acceptedEnd.state.command.mode, "off");

    const state = JSON.parse(relayEnv.STATE_KV.values.get("current-state"));
    assert.equal(state.active_meeting_id, "");
    assert.equal(state.last_event, "meeting.ended");
  });

  it("records but filters org-wide Zoom webhooks whose topics do not match", async () => {
    const relayEnv = env({ ZOOM_WEBHOOK_TOPIC_FILTER: "Board Room,Focus Room" });
    const response = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        payload: { object: { id: "other", topic: "Unrelated org meeting" } },
      }),
      relayEnv,
    );
    const responseBody = await json(response);

    assert.equal(response.status, 200);
    assert.equal(responseBody.filtered, true);

    const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "relay.started");

    const historyKey = [...relayEnv.STATE_KV.values.keys()].find((key) => key.startsWith("zoom-webhook:"));
    const record = JSON.parse(relayEnv.STATE_KV.values.get(historyKey));
    assert.equal(record.event, "meeting.started");
    assert.equal(record.outcome, "filtered");
    assert.equal(record.meeting.topic, "Unrelated org meeting");
    assert.equal(record.metadata.topic_filter_configured, true);
    assert.equal(record.metadata.topic_filter_matched, false);
    assert.deepEqual(record.metadata.topic_filter, ["Board Room", "Focus Room"]);
  });

  it("does not filter Zoom webhooks when topic filter config is missing or blank", async () => {
    for (const overrides of [{}, { ZOOM_WEBHOOK_TOPIC_FILTER: "" }, { ZOOM_WEBHOOK_TOPIC_FILTERS: "" }]) {
      const relayEnv = env(overrides);
      const response = await worker.fetch(
        await signedZoomRequest("https://relay.test/zoom/webhook?device_id=board-room-a", {
          event: "meeting.started",
          payload: { object: { id: "123", topic: "Any org meeting" } },
        }),
        relayEnv,
      );

      assert.equal(response.status, 200);
      const responseBody = await json(response);
      assert.equal(responseBody.filtered, undefined);

      const state = JSON.parse(relayEnv.STATE_KV.values.get("current-state"));
      assert.equal(state.last_event, "meeting.started");
      assert.equal(state.active_meeting_id, "123");
    }
  });

  it("uses device-specific Zoom topic filters when the webhook identifies a device", async () => {
    const relayEnv = env({
      ZOOM_WEBHOOK_TOPIC_FILTERS: JSON.stringify({
        "board-room-a": ["Board Room A"],
        "board-room-b": "Board Room B",
      }),
    });

    const filtered = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/board-room-a/webhook", {
        event: "meeting.started",
        payload: { object: { id: "a-other", topic: "Board Room B planning" } },
      }),
      relayEnv,
    );
    assert.equal(filtered.status, 200);
    assert.equal((await json(filtered)).filtered, true);

    const accepted = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/board-room-b/webhook", {
        event: "meeting.started",
        payload: { object: { id: "b-meeting", topic: "Board Room B planning" } },
      }),
      relayEnv,
    );
    assert.equal(accepted.status, 200);
    assert.equal((await json(accepted)).filtered, undefined);

    const historyRecords = [...relayEnv.STATE_KV.values.entries()]
      .filter(([key]) => key.startsWith("zoom-webhook:"))
      .map(([, value]) => JSON.parse(value));
    const filteredRecord = historyRecords.find((record) => record.outcome === "filtered");
    const acceptedRecord = historyRecords.find((record) => record.outcome === "accepted");

    assert.equal(filteredRecord.metadata.device_id, "board-room-a");
    assert.deepEqual(filteredRecord.metadata.topic_filter, ["Board Room A"]);
    assert.equal(acceptedRecord.metadata.device_id, "board-room-b");
    assert.deepEqual(acceptedRecord.metadata.topic_filter, ["Board Room B"]);
    assert.equal(acceptedRecord.state.active_meeting_id, "b-meeting");
  });

  it("stores meeting.ended as off", async () => {
    const relayEnv = env();
    await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        payload: { object: { id: "123", topic: "Demo" } },
      }),
      relayEnv,
    );

    const response = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.ended",
        payload: { object: { id: "123", topic: "Demo" } },
      }),
      relayEnv,
    );
    assert.equal(response.status, 200);

    const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "meeting.ended");
  });

  it("checks the schedule on meeting.ended and starts the empty-room warning when the next meeting is within 15 minutes", async () => {
    const relayEnv = env({
      MICROSOFT_TENANT_ID: "tenant",
      MICROSOFT_CLIENT_ID: "client",
      MICROSOFT_CLIENT_SECRET: "secret",
      MICROSOFT_CALENDAR_USER_ID: "room@example.com",
    });
    const nextStart = new Date(Date.now() + 14 * 60 * 1000).toISOString();
    const nextEnd = new Date(Date.now() + 44 * 60 * 1000).toISOString();
    const originalFetch = globalThis.fetch;
    const requestedUrls = [];

    await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        payload: { object: { id: "active", topic: "Active" } },
      }),
      relayEnv,
    );

    globalThis.fetch = async (url) => {
      const rawUrl = String(url);
      requestedUrls.push(rawUrl);
      if (rawUrl === "https://login.microsoftonline.com/tenant/oauth2/v2.0/token") {
        return new Response(JSON.stringify({ access_token: "schedule-token", expires_in: 3600 }), { status: 200 });
      }
      if (rawUrl.includes("https://graph.microsoft.com/v1.0/users/room%40example.com/calendarView?")) {
        return new Response(
          JSON.stringify({
            value: [
              {
                id: "next",
                subject: "Next",
                start: { dateTime: nextStart, timeZone: "UTC" },
                end: { dateTime: nextEnd, timeZone: "UTC" },
              },
            ],
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ meetings: [] }), { status: 200 });
    };

    try {
      const response = await worker.fetch(
        await signedZoomRequest("https://relay.test/zoom/webhook", {
          event: "meeting.ended",
          payload: { object: { id: "active", topic: "Active" } },
        }),
        relayEnv,
      );
      assert.equal(response.status, 200);

      const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
      assert.deepEqual(state.command, {
        mode: "meeting_status",
        state: "starting_soon",
        minutes: 15,
      });
      assert.equal(state.last_event, "schedule.upcoming");
      assert.ok(requestedUrls.some((url) => url === "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"));
      assert.ok(requestedUrls.some((url) => url.includes("/users/room%40example.com/calendarView?")));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("does not let the post-ended schedule check resurrect an active calendar window", async () => {
    const relayEnv = env({
      MICROSOFT_TENANT_ID: "ended-tenant",
      MICROSOFT_CLIENT_ID: "ended-client",
      MICROSOFT_CLIENT_SECRET: "ended-secret",
      MICROSOFT_CALENDAR_USER_ID: "room@example.com",
    });
    const activeStart = new Date(Date.now() - 60 * 1000).toISOString();
    const activeEnd = new Date(Date.now() + 60 * 1000).toISOString();
    const originalFetch = globalThis.fetch;

    await relayEnv.STATE_KV.put(
      "current-state",
      JSON.stringify({
        v: 1,
        command: { mode: "meeting_status", state: "ending_soon", minutes: 5 },
        updated_at: new Date(Date.now() - 15 * 1000).toISOString(),
        last_event: "schedule.ending_soon",
        source: "schedule",
        zoom_event_ts: 0,
        in_use: true,
        active_meeting_id: "calendar-active",
        active_topic: "Board Room",
        active_meeting_start_at: activeStart,
        active_meeting_end_at: activeEnd,
        next_meeting_id: "",
        next_meeting_topic: "",
        next_meeting_start_at: "",
        next_meeting_end_at: "",
        next_meeting_minutes: null,
        meeting: { id: "calendar-active", uuid: "", topic: "Board Room" },
      }),
    );

    globalThis.fetch = async (url) => {
      const rawUrl = String(url);
      if (rawUrl === "https://login.microsoftonline.com/ended-tenant/oauth2/v2.0/token") {
        return new Response(JSON.stringify({ access_token: "schedule-token", expires_in: 3600 }), { status: 200 });
      }
      if (rawUrl.includes("https://graph.microsoft.com/v1.0/users/room%40example.com/calendarView?")) {
        return new Response(
          JSON.stringify({
            value: [
              {
                id: "calendar-active",
                subject: "Board Room",
                start: { dateTime: activeStart, timeZone: "UTC" },
                end: { dateTime: activeEnd, timeZone: "UTC" },
              },
            ],
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ value: [] }), { status: 200 });
    };

    try {
      const response = await worker.fetch(
        await signedZoomRequest("https://relay.test/zoom/webhook", {
          event: "meeting.ended",
          payload: { object: { id: "zoom-active", topic: "Board Room" } },
        }),
        relayEnv,
      );
      assert.equal(response.status, 200);

      const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
      assert.deepEqual(state.command, { mode: "off" });
      assert.equal(state.last_event, "meeting.ended");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("ignores older Zoom events that arrive out of order", async () => {
    const relayEnv = env();

    await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.ended",
        event_ts: 2000,
        payload: { object: { id: "123", topic: "Demo" } },
      }),
      relayEnv,
    );

    const lateStart = await worker.fetch(
      await signedZoomRequest("https://relay.test/zoom/webhook", {
        event: "meeting.started",
        event_ts: 1000,
        payload: { object: { id: "123", topic: "Demo" } },
      }),
      relayEnv,
    );
    const lateStartBody = await json(lateStart);

    assert.equal(lateStart.status, 200);
    assert.equal(lateStartBody.stale, true);

    const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "meeting.ended");
  });

  it("maps upcoming scheduled meetings to starting_soon", () => {
    const now = Date.parse("2026-06-04T20:00:00Z");
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "starting-soon",
          topic: "Demo",
          start_time: "2026-06-04T20:04:30Z",
          duration: 30,
        },
      ],
      { v: 1, command: { mode: "off" }, last_event: "meeting.ended" },
      env({ SCHEDULE_LOOKAHEAD_MINUTES: "5" }),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(schedule, {
      v: 1,
      command: { mode: "off" },
      last_event: "meeting.ended",
    });

    assert.deepEqual(state.command, {
      mode: "meeting_status",
      state: "starting_soon",
      minutes: 5,
    });
    assert.equal(state.last_event, "schedule.upcoming");
  });

  it("uses a 15 minute upcoming window for an empty room by default", () => {
    const now = Date.parse("2026-06-04T20:00:00Z");
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "empty-room-warning",
          topic: "Demo",
          start_time: "2026-06-04T20:14:30Z",
          duration: 30,
        },
      ],
      { v: 1, command: { mode: "off" }, last_event: "meeting.ended" },
      env(),
      now,
    );

    assert.equal(schedule.upcoming.minutes, 15);
    assert.equal(schedule.upcoming.meeting.id, "empty-room-warning");
  });

  it("promotes a schedule-only upcoming meeting to active once it starts", () => {
    const now = Date.parse("2026-06-04T20:05:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "starting_soon", minutes: 5 },
      in_use: false,
      next_meeting_id: "calendar-event-id",
      next_meeting_topic: "Demo",
      next_meeting_start_at: "2026-06-04T20:00:00.000Z",
      next_meeting_end_at: "2026-06-04T20:30:00.000Z",
      last_event: "schedule.upcoming",
      source: "schedule",
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "calendar-event-id",
          topic: "Demo",
          start_time: "2026-06-04T20:00:00Z",
          duration: 30,
        },
      ],
      current,
      env(),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(schedule, current, env(), now);

    assert.deepEqual(state.command, { mode: "meeting_status", state: "in_progress" });
    assert.equal(state.last_event, "schedule.active");
    assert.equal(state.in_use, true);
    assert.equal(state.active_meeting_id, "calendar-event-id");
    assert.equal(state.active_meeting_start_at, "2026-06-04T20:00:00.000Z");
    assert.equal(state.active_meeting_end_at, "2026-06-04T20:30:00.000Z");
  });

  it("uses a 5 minute upcoming window while a meeting is already active", () => {
    const now = Date.parse("2026-06-04T20:00:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "in_progress" },
      in_use: true,
      active_meeting_id: "active",
      active_topic: "Active",
      last_event: "meeting.started",
      source: "zoom",
      meeting: { id: "active", topic: "Active" },
    };
    const outsideActiveWindow = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "too-far-out",
          topic: "Demo",
          start_time: "2026-06-04T20:14:30Z",
          duration: 30,
        },
      ],
      current,
      env(),
      now,
    );
    const insideActiveWindow = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "active-room-warning",
          topic: "Demo",
          start_time: "2026-06-04T20:04:30Z",
          duration: 30,
        },
      ],
      current,
      env(),
      now,
    );

    assert.equal(outsideActiveWindow.upcoming, null);
    assert.equal(insideActiveWindow.upcoming.minutes, 5);
    assert.equal(insideActiveWindow.upcoming.meeting.id, "active-room-warning");
  });

  it("maps active meetings near scheduled end to ending_soon", () => {
    const now = Date.parse("2026-06-04T20:25:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "in_progress" },
      in_use: true,
      active_meeting_id: "zoom-active-id",
      active_topic: "Demo",
      last_event: "meeting.started",
      source: "zoom",
      zoom_event_ts: now - 25 * 60000,
      meeting: { id: "zoom-active-id", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "calendar-event-id",
          topic: "Demo",
          start_time: "2026-06-04T20:00:00Z",
          duration: 30,
        },
      ],
      current,
      env({ ENDING_SOON_MINUTES: "5" }),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, {
      mode: "meeting_status",
      state: "ending_soon",
      minutes: 5,
    });
    assert.equal(state.last_event, "schedule.ending_soon");
  });

  it("keeps an early-ended scheduled meeting off", () => {
    const now = Date.parse("2026-06-04T20:26:00Z");
    const current = {
      v: 1,
      command: { mode: "off" },
      in_use: false,
      last_event: "meeting.ended",
      source: "zoom",
      zoom_event_ts: now - 1000,
      meeting: { id: "ended-early", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "ended-early",
          topic: "Demo",
          start_time: "2026-06-04T20:00:00Z",
          duration: 30,
        },
      ],
      current,
      env({ ENDING_SOON_MINUTES: "5" }),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "meeting.ended");
  });

  it("keeps active state during the scheduled end grace window", () => {
    const now = Date.parse("2026-06-04T20:31:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "ending_soon", minutes: 1 },
      in_use: true,
      active_meeting_id: "missed-ended",
      active_topic: "Demo",
      active_meeting_start_at: "2026-06-04T20:00:00.000Z",
      active_meeting_end_at: "2026-06-04T20:30:00.000Z",
      last_event: "schedule.ending_soon",
      source: "schedule",
      zoom_event_ts: now - 31 * 60000,
      meeting: { id: "missed-ended", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "missed-ended",
          topic: "Demo",
          start_time: "2026-06-04T20:00:00Z",
          duration: 30,
        },
      ],
      current,
      env({ ENDING_SOON_MINUTES: "5" }),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, { mode: "meeting_status", state: "ending_soon", minutes: 1 });
    assert.equal(state.last_event, "schedule.ending_soon");
  });

  it("clears active state after the scheduled end grace window", () => {
    const now = Date.parse("2026-06-04T20:36:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "ending_soon", minutes: 1 },
      in_use: true,
      active_meeting_id: "missed-ended",
      active_topic: "Demo",
      active_meeting_start_at: "2026-06-04T20:00:00.000Z",
      active_meeting_end_at: "2026-06-04T20:30:00.000Z",
      last_event: "schedule.ending_soon",
      source: "schedule",
      zoom_event_ts: now - 36 * 60000,
      meeting: { id: "missed-ended", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "missed-ended",
          topic: "Demo",
          start_time: "2026-06-04T20:00:00Z",
          duration: 30,
        },
      ],
      current,
      env({ ENDING_SOON_MINUTES: "5", SCHEDULE_END_CLEAR_GRACE_MINUTES: "5" }),
      now,
    );
    const state = testInternals.stateFromScheduleStatus(
      schedule,
      current,
      env({ SCHEDULE_END_CLEAR_GRACE_MINUTES: "5" }),
    );

    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "schedule.end_grace_clear");
  });

  it("clears a schedule-only meeting at the scheduled end when Zoom never started", () => {
    const now = Date.parse("2026-06-04T20:30:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "starting_soon", minutes: 15 },
      in_use: false,
      next_meeting_id: "never-started",
      next_meeting_topic: "Demo",
      next_meeting_start_at: "2026-06-04T20:00:00.000Z",
      next_meeting_end_at: "2026-06-04T20:30:00.000Z",
      last_event: "schedule.upcoming",
      source: "schedule",
    };
    const schedule = testInternals.scheduleStatusFromMeetings([], current, env(), now);
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, { mode: "off" });
    assert.equal(state.last_event, "schedule.end_clear");
  });

  it("loads scheduled meetings from Microsoft calendar", async () => {
    const originalFetch = globalThis.fetch;
    const requestedUrls = [];
    globalThis.fetch = async (url) => {
      requestedUrls.push(String(url));
      if (String(url) === "https://login.microsoftonline.com/list-tenant/oauth2/v2.0/token") {
        return new Response(JSON.stringify({ access_token: "graph-token", expires_in: 3600 }), { status: 200 });
      }
      if (String(url).includes("https://graph.microsoft.com/v1.0/users/room%40example.com/calendarView?")) {
        return new Response(
          JSON.stringify({
            value: [
              {
                id: "ended-active",
                iCalUId: "ical-ended-active",
                subject: "Demo",
                start: { dateTime: "2026-06-04T23:30:00.0000000", timeZone: "UTC" },
                end: { dateTime: "2026-06-04T23:34:00.0000000", timeZone: "UTC" },
              },
            ],
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ meetings: [] }), { status: 200 });
    };

    try {
      const meetings = await testInternals.listScheduleMeetings(
        env({
          MICROSOFT_TENANT_ID: "list-tenant",
          MICROSOFT_CLIENT_ID: "list-client",
          MICROSOFT_CLIENT_SECRET: "list-secret",
          MICROSOFT_CALENDAR_USER_ID: "room@example.com",
        }),
        Date.parse("2026-06-04T23:31:00Z"),
      );
      assert.deepEqual(meetings, [
        {
          id: "ended-active",
          uuid: "ical-ended-active",
          topic: "Demo",
          start_time: "2026-06-04T23:30:00.000Z",
          duration: 4,
        },
      ]);
      assert.ok(requestedUrls.some((url) => url.includes("/users/room%40example.com/calendarView?")));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("does not let the schedule clear active state when the meeting disappears", () => {
    const now = Date.parse("2026-06-04T20:31:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "in_progress" },
      in_use: true,
      active_meeting_id: "missing-active",
      active_topic: "Demo",
      last_event: "schedule.active",
      source: "schedule",
      zoom_event_ts: now - 31 * 60000,
      meeting: { id: "missing-active", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings([], current, env(), now);
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, { mode: "meeting_status", state: "in_progress" });
    assert.equal(state.last_event, "schedule.active");
  });

  it("keeps fresh Zoom active state when the meeting is absent from the schedule", () => {
    const now = Date.parse("2026-06-04T20:05:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "in_progress" },
      in_use: true,
      active_meeting_id: "instant-active",
      active_topic: "Demo",
      last_event: "meeting.started",
      source: "zoom",
      zoom_event_ts: now - 1000,
      meeting: { id: "instant-active", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings([], current, env(), now);
    const state = testInternals.stateFromScheduleStatus(schedule, current);

    assert.deepEqual(state.command, { mode: "meeting_status", state: "in_progress" });
    assert.equal(state.last_event, "meeting.started");
  });

  it("supports protected simulate endpoints for upcoming and ending soon", async () => {
    const relayEnv = env();
    const upcoming = await worker.fetch(
      new Request("https://relay.test/simulate/upcoming?minutes=4", {
        headers: { Authorization: "Bearer admin-token" },
      }),
      relayEnv,
    );
    assert.equal(upcoming.status, 200);
    assert.deepEqual((await json(upcoming)).state.command, {
      mode: "meeting_status",
      state: "starting_soon",
      minutes: 4,
    });

    const ending = await worker.fetch(
      new Request("https://relay.test/simulate/ending-soon?minutes=3", {
        method: "POST",
        headers: { Authorization: "Bearer admin-token" },
      }),
      relayEnv,
    );
    assert.equal(ending.status, 200);
    assert.deepEqual((await json(ending)).state.command, {
      mode: "meeting_status",
      state: "ending_soon",
      minutes: 3,
    });
  });

  it("supports protected simulate ota endpoint for forcing a device OTA check", async () => {
    const relayEnv = env();
    const response = await worker.fetch(
      new Request("https://relay.test/simulate/ota", {
        method: "POST",
        headers: { Authorization: "Bearer admin-token" },
      }),
      relayEnv,
    );
    assert.equal(response.status, 200);
    const body = await json(response);
    assert.equal(body.state.last_event, "simulate.ota.requested");
    assert.match(body.state.ota_check_requested_at, /^\d{4}-\d{2}-\d{2}T/);
    assert.deepEqual(body.state.command, { mode: "off" });

    const state = await json(await worker.fetch(new Request("https://relay.test/device/state"), relayEnv));
    assert.equal(state.ota_check_requested_at, body.state.ota_check_requested_at);
    assert.equal(state.last_event, "simulate.ota.requested");
  });
});
