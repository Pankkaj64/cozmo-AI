type Fields = Record<string, unknown>;
type Level = "info" | "warn" | "error";

export function logStep(step: string, fields: Fields = {}, level: Level = "info") {
  try {
    const entry = { time: new Date().toISOString(), level, step, fields };
    console[level](`[frontend] ${step}`, fields);
    // Vite's existing development socket forwards browser events to its terminal.
    // Production builds retain browser-console logs without sending telemetry.
    import.meta.hot?.send("app:log", entry);
  } catch {
    // Observability must never interrupt camera capture or an upload.
  }
}

export function errorDetails(error: unknown): Fields {
  return error instanceof Error
    ? { error: error.message, errorType: error.name }
    : { error: String(error) };
}

export async function loggedFetch(url: string, options?: RequestInit, timeoutMs = /\/(frames|research|finish)$/.test(url) ? 210000 : /\/video$/.test(url) ? 60000 : 15000): Promise<Response> {
  const started = Date.now();
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  options?.signal?.addEventListener("abort", abort, { once: true });
  if (options?.signal?.aborted) controller.abort();
  const deadline = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const fields = { method: options?.method || "GET", url };
  logStep("http.started", fields);
  const waiting = window.setInterval(() => {
    logStep("http.waiting", { ...fields, elapsedSeconds: Math.round((Date.now() - started) / 1000) });
  }, 10000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    logStep("http.finished", { ...fields, status: response.status, elapsedMs: Date.now() - started }, response.ok ? "info" : "warn");
    return response;
  } catch (error) {
    logStep("http.failed", { ...fields, ...errorDetails(error), elapsedMs: Date.now() - started }, "error");
    if (timedOut) throw new Error("The server did not respond in time. Captured frames remain saved; retry saving the sweep after the server reconnects.");
    throw error;
  } finally {
    window.clearTimeout(deadline);
    options?.signal?.removeEventListener("abort", abort);
    window.clearInterval(waiting);
  }
}
