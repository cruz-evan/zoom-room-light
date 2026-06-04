DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zoom Room Light</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #111418;
      color: #f4f7fb;
    }

    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at 50% 10%, rgba(255, 255, 255, 0.10), transparent 28rem),
        #111418;
    }

    main {
      width: min(44rem, calc(100vw - 2rem));
      display: grid;
      gap: 1.25rem;
      text-align: center;
    }

    .lamp {
      width: min(18rem, 70vw);
      aspect-ratio: 1;
      margin: 0 auto;
      border-radius: 50%;
      border: 0.75rem solid rgba(255, 255, 255, 0.14);
      background: var(--lamp, #33d17a);
      box-shadow:
        0 0 2rem var(--glow, rgba(51, 209, 122, 0.55)),
        0 0 7rem var(--glow, rgba(51, 209, 122, 0.35)),
        inset 0 0 4rem rgba(255, 255, 255, 0.28);
      transition: background 180ms ease, box-shadow 180ms ease;
    }

    .status {
      font-size: clamp(2.5rem, 5rem, 5.75rem);
      font-weight: 800;
      line-height: 0.95;
    }

    .meta {
      display: grid;
      gap: 0.4rem;
      color: #c9d3df;
      font-size: 1rem;
    }

    #next {
      color: #f6d32d;
      min-height: 1.25rem;
    }

    .controls {
      display: flex;
      justify-content: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }

    button {
      appearance: none;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 0.5rem;
      padding: 0.7rem 0.95rem;
      background: rgba(255, 255, 255, 0.08);
      color: inherit;
      font: inherit;
      cursor: pointer;
    }

    button:hover {
      background: rgba(255, 255, 255, 0.14);
    }

    .events {
      display: grid;
      gap: 0.5rem;
      margin-top: 0.5rem;
      text-align: left;
    }

    .event-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 0.75rem;
      align-items: baseline;
      padding: 0.65rem 0;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
      color: #dce5ee;
    }

    .event-row small {
      color: #9ba8b5;
      white-space: nowrap;
    }
  </style>
</head>
<body>
  <main>
    <div id="lamp" class="lamp" aria-hidden="true"></div>
    <div id="status" class="status">FREE</div>
    <div class="meta">
      <div id="topic"></div>
      <div id="next"></div>
      <div id="event">Connecting...</div>
      <div id="updated"></div>
    </div>
    <div class="controls">
      <button type="button" data-url="/simulate/start">Simulate Start</button>
      <button type="button" data-url="/simulate/end">Simulate End</button>
      <button type="button" data-url="/simulate/join">Simulate Join</button>
      <button type="button" data-url="/simulate/leave">Simulate Leave</button>
      <button type="button" data-url="/simulate/upcoming">Simulate Upcoming</button>
      <button type="button" data-url="/simulate/ending-soon">Simulate Ending Soon</button>
      <button type="button" data-url="/simulate/clear-upcoming">Clear Upcoming</button>
      <button type="button" data-url="/schedule/check">Check Schedule</button>
      <button type="button" data-url="/reset">Reset</button>
    </div>
    <div id="events" class="events" aria-live="polite"></div>
  </main>
  <script>
    const colorMap = {
      green: ["#33d17a", "rgba(51, 209, 122, 0.55)"],
      yellow: ["#f6d32d", "rgba(246, 211, 45, 0.55)"],
      orange: ["#ff9f1c", "rgba(255, 159, 28, 0.55)"],
      red: ["#ff4d4d", "rgba(255, 77, 77, 0.58)"],
      purple: ["#c061cb", "rgba(192, 97, 203, 0.55)"]
    };

    function update(state) {
      const [lamp, glow] = colorMap[state.color] || colorMap.purple;
      document.documentElement.style.setProperty("--lamp", lamp);
      document.documentElement.style.setProperty("--glow", glow);
      document.getElementById("status").textContent = state.label || state.color;
      document.getElementById("topic").textContent = state.active_topic || "";
      document.getElementById("next").textContent = nextMeetingText(state);
      document.getElementById("event").textContent = state.last_event || "";
      document.getElementById("updated").textContent = state.updated_at
        ? new Date(state.updated_at).toLocaleString()
        : "";
      renderEvents(state.recent_events || []);
    }

    function nextMeetingText(state) {
      if (state.minutes_until_end !== null && state.minutes_until_end !== undefined) {
        const minutes = state.minutes_until_end;
        const unit = minutes === 1 ? "minute" : "minutes";
        return `Meeting ends in ${minutes} ${unit}`;
      }
      if (!state.next_meeting_id) return "";
      const topic = state.next_meeting_topic || "Scheduled meeting";
      const minutes = state.minutes_until_next;
      if (Number.isInteger(minutes)) {
        const unit = minutes === 1 ? "minute" : "minutes";
        return `${topic} starts in ${minutes} ${unit}`;
      }
      return `${topic} starts soon`;
    }

    function renderEvents(events) {
      const root = document.getElementById("events");
      root.replaceChildren(...events.map((item) => {
        const row = document.createElement("div");
        row.className = "event-row";

        const label = document.createElement("div");
        label.textContent = [
          item.event,
          item.topic || item.meeting_id
        ].filter(Boolean).join(" | ");

        const time = document.createElement("small");
        time.textContent = item.received_at ? new Date(item.received_at).toLocaleTimeString() : "";

        row.append(label, time);
        return row;
      }));
    }

    async function loadInitialState() {
      const response = await fetch("/state");
      update(await response.json());
    }

    loadInitialState();

    const events = new EventSource("/events");
    events.onmessage = (event) => update(JSON.parse(event.data));
    events.onerror = () => {
      document.getElementById("event").textContent = "stream reconnecting";
    };

    document.querySelectorAll("button[data-url]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await fetch(button.dataset.url);
        update(await response.json());
      });
    });
  </script>
</body>
</html>
"""
