import { Suspense, lazy, type ComponentType } from "react";
import { Navigate, createBrowserRouter, useLocation } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { useAuthStore } from "@/stores/auth";

const Login = lazy(() => import("@/pages/Login").then((m) => ({ default: m.Login })));
const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const Dividends = lazy(() =>
  import("@/pages/Dividends").then((m) => ({ default: m.Dividends })),
);
const BuyPoints = lazy(() =>
  import("@/pages/BuyPoints").then((m) => ({ default: m.BuyPoints })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const Scheduler = lazy(() =>
  import("@/pages/Scheduler").then((m) => ({ default: m.Scheduler })),
);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

/**
 * Route guard. Redirects to /login when the user has no credential.
 *
 * In legacy dev mode (backend auth disabled), loopback requests are trusted by
 * the server without a token, so this guard only activates when the browser is
 * talking to a remote server that requires auth.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const hasCredential = useAuthStore((s) => s.hasCredential);
  if (!hasCredential) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export const router = createBrowserRouter([
  {
    path: "/login",
    element: wrap(Login),
  },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { path: "/", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/dividends", element: wrap(Dividends) },
      { path: "/buy-points", element: wrap(BuyPoints) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
      { path: "/scheduler", element: wrap(Scheduler) },
    ],
  },
]);
