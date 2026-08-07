/**
 * WebSocket client for the MatStat event stream.
 *
 * Mirrors the Qt log-bridge pattern: the server pushes log lines and
 * background-job events over a single socket. This module owns the
 * connection lifecycle (auto-reconnect) and dispatches parsed events to
 * registered handlers keyed by event `type`.
 *
 * The module is a singleton; import `connectWs` from anywhere.
 */

const RECONNECT_DELAY_MS = 2000;

let socket = null;
let handlers = {};
let statusCallback = null;

function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws`;
}

function dispatch(event) {
  const typeHandlers = handlers[event.type] || [];
  for (const handler of typeHandlers) {
    try {
      handler(event);
    } catch (err) {
      console.error("WS handler error:", err);
    }
  }
}

function open() {
  socket = new WebSocket(wsUrl());

  socket.addEventListener("open", () => {
    if (statusCallback) statusCallback("online");
  });

  socket.addEventListener("message", (msg) => {
    try {
      dispatch(JSON.parse(msg.data));
    } catch (err) {
      console.error("WS parse error:", err);
    }
  });

  socket.addEventListener("close", () => {
    if (statusCallback) statusCallback("offline");
    setTimeout(open, RECONNECT_DELAY_MS);
  });

  socket.addEventListener("error", () => {
    if (statusCallback) statusCallback("connecting");
  });
}

/**
 * Register an event handler. `type` is the event's `type` field (e.g. "log",
 * "job_progress"). Handlers may be added before or after the connection opens.
 */
export function onWs(type, handler) {
  (handlers[type] = handlers[type] || []).push(handler);
}

/** Set a callback invoked with "online" | "connecting" | "offline". */
export function onWsStatus(callback) {
  statusCallback = callback;
}

/** Send a message to the server (e.g. a job cancellation). */
export function sendWs(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

export function connectWs() {
  open();
}
