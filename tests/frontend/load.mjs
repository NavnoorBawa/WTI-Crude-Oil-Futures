// Loads the dashboard's real src modules through Vite's SSR transform and also exports the private
// helpers the tests need, so the tests exercise exactly the shipped code (no copies).
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PRIVATE_HELPERS = {
  "/src/Chart.jsx": ["buildActualPoints", "buildPriceViews", "formatWallTime"],
  "/src/App.jsx": [
    "normalizeQuote", "isQuoteUsable", "resolveQuote", "describeLiveRecord", "computeSizing",
    "SupplySection", "VolatilityCard",
  ],
};

export async function loadDashboard() {
  const server = await createServer({
    root: ROOT,
    configFile: false,
    logLevel: "error",
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    define: { "import.meta.env.VITE_STATIC_DATA": JSON.stringify("true") },
    plugins: [{
      name: "expose-private-helpers",
      enforce: "post",
      transform(code, id) {
        const file = Object.keys(PRIVATE_HELPERS).find((name) => id.endsWith(name));
        if (!file) return null;
        const missing = PRIVATE_HELPERS[file].filter((name) => !new RegExp(`(const|function) ${name}\\b`).test(code));
        if (missing.length) throw new Error(`${file} no longer defines ${missing.join(", ")}`);
        return { code: `${code}\nexport { ${PRIVATE_HELPERS[file].join(", ")} };\n`, map: null };
      },
    }],
    optimizeDeps: { noDiscovery: true, include: [] },
  });
  return {
    chart: await server.ssrLoadModule("/src/Chart.jsx"),
    app: await server.ssrLoadModule("/src/App.jsx"),
    format: await server.ssrLoadModule("/src/format.js"),
    close: () => server.close(),
  };
}
