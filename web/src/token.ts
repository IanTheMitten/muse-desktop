const KEY = "muse-token";

function boot(): string | null {
  const url = new URL(location.href);
  const t = url.searchParams.get("t");
  if (t) {
    try {
      sessionStorage.setItem(KEY, t);
    } catch {
      /* storage blocked: token lives for this page load only */
    }
    url.searchParams.delete("t");
    history.replaceState(history.state, "", url.toString());
    return t;
  }
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export const token: string | null = boot();

export function withToken(url: string): string {
  if (token === null) return url;
  return `${url}${url.includes("?") ? "&" : "?"}t=${encodeURIComponent(token)}`;
}

export function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (token !== null) headers.set("X-Muse-Token", token);
  return fetch(input, { ...init, headers });
}
