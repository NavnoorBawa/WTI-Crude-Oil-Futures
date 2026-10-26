// Loads the dashboard's real src modules through Vite's SSR transform and also exports the private
// helpers the tests need, so the tests exercise exactly the shipped code (no copies).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
// Module-private helpers exposed to the tests by name (the module must still define each one).
const PRIVATE_HELPERS = {
  "/src/charts/PriceChart.jsx": ["buildActualPoints", "buildPriceViews", "formatWallTime"],
};

export const readFixture = (name) =>
  JSON.parse(readFileSync(new URL(`../fixtures/${name}`, import.meta.url), "utf8"));

export const readSource = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

export async function loadDashboard() {
  const server = await createServer({
    root: ROOT,
    configFile: false,
    logLevel: "error",
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    define: {
      "import.meta.env.VITE_STATIC_DATA": JSON.stringify("true"),
      "import.meta.env.VITE_GIT_SHA": JSON.stringify(""),
    },
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
  const load = (path) => server.ssrLoadModule(path);
  return {
    load,
    chart: await load("/src/charts/PriceChart.jsx"),
    quote: await load("/src/app/quote.js"),
    format: await load("/src/lib/format.js"),
    research: await load("/src/lib/research.js"),
    assertions: await load("/src/lib/assertions.js"),
    table: await load("/src/components/DataTable.jsx"),
    theme: await load("/src/theme/chartTheme.js"),
    svg: await load("/src/charts/svgCharts.jsx"),
    sparkline: await load("/src/components/Sparkline.jsx"),
    keepHyphenated: await load("/src/components/keepHyphenated.jsx"),
    dashboard: await load("/src/app/Dashboard.jsx"),
    vrp: await load("/src/panels/VrpPanel.jsx"),
    ledger: await load("/src/panels/LedgerPanel.jsx"),
    marketStrip: await load("/src/panels/MarketStrip.jsx"),
    panel: await load("/src/components/Panel.jsx"),
    forecastChart: await load("/src/charts/ForecastChart.jsx"),
    varChart: await load("/src/charts/VarChart.jsx"),
    close: () => server.close(),
  };
}

/** The visible text of server-rendered markup: every character outside a tag, whitespace collapsed. */
export const visibleText = (html) => {
  let visible = "";
  let inTag = false;
  for (const character of html) {
    if (character === "<") inTag = true;
    else if (character === ">") { inTag = false; visible += " "; }
    else if (!inTag) visible += character;
  }
  return visible
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ");
};
