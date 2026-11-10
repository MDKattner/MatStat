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

const INITIAL_RECONNECT_MS = 2000;
const MAX_RECONNECT_MS = 30000;

let socket = null;
let handlers = {};
let statusCallback = null;
let reconnectDelay = INITIAL_RECONNECT_MS;

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
    reconnectDelay = INITIAL_RECONNECT_MS;
    if (statusCallback) statusCallback("online");
  });

  socket.addEventListener("message", (msg) => {
    try {
      dispatch(JSON.parse(msg.data));
    } catch (err) {
      console.error("WS parse error:", err);
    }
  });

  socket.addEventListener("close", (event) => {
    // 1008 = policy violation: the server rejected an unauthenticated socket.
    if (event.code === 1008) {
      window.location.href = "/login";
      return;
    }
    if (statusCallback) statusCallback("offline");
    setTimeout(open, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_MS);
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

export function connectWs() {
  open();
}
