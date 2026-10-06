export const API = import.meta.env.VITE_API_URL || "";

export async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body.detail || body.message;
    return detail ? `${fallback}: ${detail}` : `${fallback} (HTTP ${response.status})`;
  } catch {
    return `${fallback} (HTTP ${response.status})`;
  }
}
