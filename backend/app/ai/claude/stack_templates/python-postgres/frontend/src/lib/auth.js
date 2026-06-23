import api, { setToken } from "@/lib/api";

export async function register({ email, password, name }) {
  const res = await api.post("/auth/register", { email, password, name });
  if (res && res.access_token) setToken(res.access_token);
  return res;
}

export async function login({ email, password }) {
  const res = await api.post("/auth/login", { email, password });
  if (res && res.access_token) setToken(res.access_token);
  return res;
}

export async function me() {
  return api.get("/auth/me");
}

export function logout() {
  setToken(null);
}
