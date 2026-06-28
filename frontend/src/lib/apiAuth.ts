// Auth token storage and header/query helpers.
//
// Supports two modes:
// 1. Multi-user JWT mode: access + refresh tokens stored after login.
// 2. Legacy shared API key mode: a single key in localStorage (fallback when
//    the backend runs in dev mode without auth configured).
//
// Both modes surface credentials via authHeaders() / withAuthQuery(), so the
// rest of the app (api.ts, useSSE) is agnostic to the scheme.

const ACCESS_KEY = "vibe_trading_access_token";
const REFRESH_KEY = "vibe_trading_refresh_token";
const USER_KEY = "vibe_trading_user";
// Legacy shared API key (still honored by the backend dev mode).
const LEGACY_API_KEY = "vibe_trading_api_auth_key";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
}

// ---- Access / refresh tokens (JWT mode) ----

export function getAccessToken(): string {
  return window.localStorage.getItem(ACCESS_KEY) || "";
}

export function getRefreshToken(): string {
  return window.localStorage.getItem(REFRESH_KEY) || "";
}

export function setTokens(access: string, refresh: string): void {
  if (access) window.localStorage.setItem(ACCESS_KEY, access);
  if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh);
}

export function setAuthUser(user: AuthUser | null): void {
  if (user) {
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  } else {
    window.localStorage.removeItem(USER_KEY);
  }
}

export function getAuthUser(): AuthUser | null {
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function clearTokens(): void {
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export function isJwtAuthenticated(): boolean {
  return Boolean(getAccessToken());
}

// ---- Legacy shared API key mode ----

export function getApiAuthKey(): string {
  return window.localStorage.getItem(LEGACY_API_KEY) || "";
}

export function setApiAuthKey(value: string): void {
  const trimmed = value.trim();
  if (trimmed) {
    window.localStorage.setItem(LEGACY_API_KEY, trimmed);
  } else {
    window.localStorage.removeItem(LEGACY_API_KEY);
  }
}

// ---- Combined credential access ----

/**
 * Whether the client has any usable credential (JWT or legacy API key).
 * Used by the route guard to decide whether to redirect to /login.
 */
export function hasCredentials(): boolean {
  return Boolean(getAccessToken() || getApiAuthKey());
}

/**
 * Authorization header for fetch requests. Prefers the JWT access token,
 * falls back to the legacy shared API key.
 */
export function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  if (token) return { Authorization: `Bearer ${token}` };
  const key = getApiAuthKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

/**
 * Query-string credential for SSE EventSource (which cannot set headers).
 * Prefers the JWT access token, falls back to the legacy API key.
 */
export function authQuerySuffix(): string {
  const token = getAccessToken();
  if (token) return `token=${encodeURIComponent(token)}`;
  const key = getApiAuthKey();
  return key ? `api_key=${encodeURIComponent(key)}` : "";
}

export function withAuthQuery(url: string): string {
  const suffix = authQuerySuffix();
  if (!suffix) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${suffix}`;
}
