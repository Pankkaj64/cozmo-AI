// Thin fetch helpers. In development Vite proxies /api and /data to the backend.
export const API = import.meta.env.VITE_API_URL || "";

async function check(response: Response): Promise<Response> {
  if (response.ok) return response;
  let detail = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    detail = body.detail || detail;
  } catch {
    /* no JSON body */
  }
  throw new Error(detail);
}

export async function get<T>(path: string): Promise<T> {
  return (await check(await fetch(API + path))).json();
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit =
    body instanceof FormData
      ? { method: "POST", body }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) };
  return (await check(await fetch(API + path, init))).json();
}

// Evidence refs look like "data/frames/x.jpg"; the backend serves /data statically.
export const fileUrl = (ref: string) => `${API}/${ref}`;
