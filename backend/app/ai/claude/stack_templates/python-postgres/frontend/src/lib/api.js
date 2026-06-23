// Pre-configured fetch wrapper. Automatically attaches the JWT
// from localStorage, parses JSON, and surfaces server errors as
// throwable Error objects. Use this for ALL backend calls.
//
// Usage:
//   import api from "@/lib/api";
//   const plants = await api.get("/plants");
//   const created = await api.post("/plants", { name: "Monstera" });

const BASE = (
  typeof import.meta !== "undefined"
  && import.meta.env
  && import.meta.env.VITE_API_BASE
) || "/api";
const TOKEN_KEY = "auth_token";

function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // swallow -- SSR or storage disabled
  }
}

function normalizePath(path) {
  // Absolute URL -- use as-is.
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  // Already includes the API base -- use as-is, no double prefix.
  if (path.startsWith(BASE + "/") || path === BASE) {
    return path;
  }
  // Relative path -- prepend BASE. Tolerate missing leading slash.
  const sep = path.startsWith("/") ? "" : "/";
  return `${BASE}${sep}${path}`;
}

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const init = { method, headers };
  if (body !== undefined && body !== null) {
    init.body = JSON.stringify(body);
  }

  const url = normalizePath(path);
  const res = await fetch(url, init);

  // Global 401 handling: drop the token. The AuthContext will
  // detect the missing token on its next /me check and redirect
  // to login. Do NOT navigate from here -- that creates loops
  // when called from inside a render path.
  if (res.status === 401) {
    setToken(null);
  }

  // No-content response (DELETE returning 204, etc).
  if (res.status === 204) return null;

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }

  if (!res.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) ||
      `Request failed: ${res.status}`;
    const err = new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
    err.status = res.status;
    err.body = payload;
    throw err;
  }

  return payload;
}

const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  patch: (path, body) => request("PATCH", path, body),
  delete: (path) => request("DELETE", path),
  getToken,
  setToken,
};

export default api;
export { getToken, setToken };

/**
 * Defensive wrapper for TanStack Query queryFn. Use this in
 * useQuery to guarantee a non-undefined return so React Query
 * doesn't show the "data cannot be undefined" warning.
 *
 * Usage:
 *   useQuery({
 *     queryKey: ["dashboard"],
 *     queryFn: () => apiQuery("/dashboard"),
 *     enabled: !!user,
 *   })
 *
 * Returns:
 *   - parsed JSON data on success
 *   - null if endpoint returned 204
 *   - throws on non-2xx (TanStack handles the throw correctly)
 */
export async function apiQuery(path) {
  const data = await api.get(path);
  return data === undefined ? null : data;
}
