import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const PROXY_PATHS = [
  "/auth",
  "/sessions",
  "/swarm/presets",
  "/swarm/runs",
  "/mandate",
  "/live",
  "/upload",
  "/shadow-reports",
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_URL || "http://127.0.0.1:8899";
  const apiProxy = { target: apiTarget, changeOrigin: true };
  const apiProxyWithHtmlFallback = {
    ...apiProxy,
    bypass(req: { headers: { accept?: string } }) {
      if (req.headers.accept?.includes("text/html")) {
        return "/index.html";
      }
    },
  };

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      port: 5899,
      proxy: {
        ...Object.fromEntries(PROXY_PATHS.map((p) => [p, apiProxy])),
        // SPA RunDetail page — only the two-segment ``/runs/{id}``
        // form should fall back to ``index.html`` on browser navigation.
        // ``/runs/{id}/code`` and ``/runs/{id}/pine`` are API-only and
        // must keep proxying to the backend even when Accept is text/html.
        "^/runs/[^/]+/?$": apiProxyWithHtmlFallback,
        "/runs": apiProxy,
        "/correlation": apiProxyWithHtmlFallback,
        // Same SPA/API split as ``/correlation``: browser navigation to
        // ``/dividends`` gets index.html; XHR/fetch with query params
        // (universe, min_yield, ...) proxies to the backend screener.
        "/dividends": apiProxyWithHtmlFallback,
        // The browser navigates to the ``/settings`` SPA page, while every
        // ``/settings/*`` API call must reach the backend. Route the whole
        // prefix through the HTML-fallback proxy so only the bare page path
        // serves index.html and nested endpoints (llm, data-sources, email,
        // email/test, ...) are never shadowed by the SPA shell.
        "/settings": apiProxyWithHtmlFallback,
        // Same SPA/API split as ``/settings``: bare ``/scheduler`` is the
        // React page; ``/scheduler/tasks|presets|status|...`` are APIs.
        "/scheduler": apiProxyWithHtmlFallback,
        "^/alpha(?:/|$)": apiProxy,
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-charts": ["echarts"],
          },
        },
      },
    },
  };
});
