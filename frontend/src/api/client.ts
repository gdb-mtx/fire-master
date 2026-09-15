const TIMEOUT_MS = 20_000;

async function fetchWithTimeout(
  url: string,
  opts?: RequestInit,
  timeoutMs: number = TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
}

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("token");
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export async function fetchJSON<T>(
  url: string,
  opts?: RequestInit,
  timeoutMs: number = TIMEOUT_MS,
): Promise<T> {
  const res = await fetchWithTimeout(url, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...opts?.headers,
    },
  }, timeoutMs);

  // 401 on a normal API call = expired/invalid session -> back to login.
  // A 401 from the login endpoint itself is just "wrong credentials" — redirecting
  // there reloads the page, loses the error, and (with demo auto-login) loops forever.
  if (res.status === 401 && !url.includes("/auth/login")) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail)
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }

  return res.json() as Promise<T>;
}

export async function postJSON<T>(
  url: string,
  body?: unknown,
): Promise<T> {
  return fetchJSON<T>(url, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function patchJSON<T>(
  url: string,
  body?: unknown,
): Promise<T> {
  return fetchJSON<T>(url, {
    method: "PATCH",
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function putJSON<T>(
  url: string,
  body?: unknown,
): Promise<T> {
  return fetchJSON<T>(url, {
    method: "PUT",
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function deleteJSON<T = { ok: boolean }>(
  url: string,
): Promise<T> {
  return fetchJSON<T>(url, { method: "DELETE" });
}
