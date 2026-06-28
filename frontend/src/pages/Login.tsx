import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, LineChart } from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth";

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "text-sm font-medium";

interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: { id: string; email: string; name: string };
}

export function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const endpoint = mode === "login" ? "/auth/login" : "/auth/register";
      const body =
        mode === "login"
          ? { email, password }
          : { email, password, name };
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = "Authentication failed";
        try {
          const data = await res.json();
          detail = data.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      const data = (await res.json()) as AuthResponse;
      login(data.access_token, data.refresh_token, data.user);
      toast.success(mode === "login" ? "Signed in" : "Account created");
      navigate("/");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === "login";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <LineChart className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {isLogin ? "Sign in to Vibe-Trading" : "Create your account"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {isLogin
              ? "Enter your credentials to access your workspace."
              : "Register to start your finance research workspace."}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-lg border bg-card p-6 shadow-sm">
          {!isLogin && (
            <label className="grid gap-1.5">
              <span className={labelClass}>Display name (optional)</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className={fieldClass}
                placeholder="Your name"
                autoComplete="name"
              />
            </label>
          )}
          <label className="grid gap-1.5">
            <span className={labelClass}>Email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={fieldClass}
              placeholder="you@example.com"
              autoComplete="email"
              autoFocus
            />
          </label>
          <label className="grid gap-1.5">
            <span className={labelClass}>Password</span>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={fieldClass}
              placeholder={isLogin ? "Your password" : "At least 8 characters"}
              autoComplete={isLogin ? "current-password" : "new-password"}
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {isLogin ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="text-center text-sm text-muted-foreground">
          {isLogin ? "Don't have an account? " : "Already have an account? "}
          <button
            type="button"
            onClick={() => setMode(isLogin ? "register" : "login")}
            className="font-medium text-primary hover:underline"
          >
            {isLogin ? "Register" : "Sign in"}
          </button>
        </p>
      </div>
    </div>
  );
}
