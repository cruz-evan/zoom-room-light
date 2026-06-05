import assert from "node:assert/strict";
import { describe, it } from "node:test";

import worker, { testInternals } from "../src/worker.js";

class MemoryKv {
  constructor() {
    this.values = new Map();
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

  async put(key, value) {
    this.values.set(key, value);
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
    const response = await worker.fetch(
      new Request("https://relay.test/zoom/webhook", {
        method: "POST",
        body: JSON.stringify({ event: "meeting.started", payload: {} }),
      }),
      env(),
    );
    assert.equal(response.status, 401);
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

  it("maps active meetings near scheduled end to ending_soon", () => {
    const now = Date.parse("2026-06-04T20:25:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "in_progress" },
      in_use: true,
      active_meeting_id: "ending-soon",
      active_topic: "Demo",
      last_event: "meeting.started",
      source: "zoom",
      zoom_event_ts: now - 25 * 60000,
      meeting: { id: "ending-soon", topic: "Demo" },
    };
    const schedule = testInternals.scheduleStatusFromMeetings(
      [
        {
          id: "ending-soon",
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

  it("keeps active state after the scheduled end until Zoom sends ended", () => {
    const now = Date.parse("2026-06-04T20:31:00Z");
    const current = {
      v: 1,
      command: { mode: "meeting_status", state: "ending_soon", minutes: 1 },
      in_use: true,
      active_meeting_id: "missed-ended",
      active_topic: "Demo",
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

    assert.deepEqual(state.command, { mode: "meeting_status", state: "in_progress" });
    assert.equal(state.last_event, "schedule.end_clear");
  });

  it("loads scheduled meetings when upcoming endpoints are empty", async () => {
    const originalFetch = globalThis.fetch;
    const requestedUrls = [];
    globalThis.fetch = async (url) => {
      requestedUrls.push(String(url));
      if (String(url).includes("type=scheduled")) {
        return new Response(
          JSON.stringify({
            meetings: [
              {
                id: "ended-active",
                topic: "Demo",
                start_time: "2026-06-04T23:30:00Z",
                duration: 4,
              },
            ],
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ meetings: [] }), { status: 200 });
    };

    try {
      const meetings = await testInternals.listZoomScheduleMeetings(env(), "token");
      assert.deepEqual(
        meetings.map((meeting) => meeting.id),
        ["ended-active"],
      );
      assert.ok(requestedUrls.some((url) => url.includes("type=scheduled")));
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
});
