import { create } from "zustand";
import { toast } from "sonner";
import {
  type AuthUser,
  clearTokens,
  getAuthUser,
  hasCredentials,
  setAuthUser,
  setTokens,
} from "@/lib/apiAuth";

export interface AuthState {
  user: AuthUser | null;
  // True when the browser holds a credential (JWT or legacy key). UI uses this
  // to decide whether to show the login redirect.
  hasCredential: boolean;
  // Multi-user login is available only when the backend reports auth enabled.
  authEnabled: boolean;
  // Hydrated from localStorage on store creation.
  hydrated: boolean;
}

interface AuthActions {
  hydrate: () => void;
  refreshAuthStatus: () => void;
  login: (access: string, refresh: string, user: AuthUser) => void;
  logout: (opts?: { silent?: boolean }) => void;
  setAuthEnabled: (enabled: boolean) => void;
}

export type AuthStore = AuthState & AuthActions;

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  hasCredential: false,
  authEnabled: false,
  hydrated: false,

  hydrate: () => {
    set({
      user: getAuthUser(),
      hasCredential: hasCredentials(),
      hydrated: true,
    });
  },

  refreshAuthStatus: () => {
    set({ user: getAuthUser(), hasCredential: hasCredentials() });
  },

  login: (access, refresh, user) => {
    setTokens(access, refresh);
    setAuthUser(user);
    set({ user, hasCredential: true });
  },

  logout: (opts) => {
    clearTokens();
    set({ user: null, hasCredential: false });
    if (!opts?.silent) toast.success("Signed out");
  },

  setAuthEnabled: (enabled) => set({ authEnabled: enabled }),
}));

// Fetch the backend auth status (enabled/disabled) on module load so the UI
// knows whether to present a login screen or operate in legacy dev mode.
export async function probeAuthStatus(): Promise<void> {
  try {
    const res = await fetch("/auth/status");
    if (res.ok) {
      const data = await res.json();
      useAuthStore.getState().setAuthEnabled(Boolean(data.enabled));
    }
  } catch {
    // Backend unreachable; default to disabled (legacy dev mode).
  }
}

// Initialize from localStorage immediately on import.
useAuthStore.getState().hydrate();
void probeAuthStatus();
