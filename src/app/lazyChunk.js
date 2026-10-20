import { lazy } from "react";

// Chart modules load in their own chunks after the verdict above them has rendered. A failed chunk
// load (a flaky link, or a request racing a deploy) must not be final: React.lazy keeps a rejected
// import forever, so a fresh lazy component is swapped in, which the figure's error boundary
// renders when it resets on the next data refresh. Browsers also cache a failed module fetch, so
// the retry asks for the chunk under a new URL; Chromium and Firefox name it in the error,
// elsewhere the import is simply repeated.
const failedModuleUrl = (error) =>
  /\bhttps?:\/\/[^\s'"]+?\.js(?=$|[\s'"?#])/.exec(String(error?.message ?? ""))?.[0] ?? null;

/**
 * @param {() => Promise<object>} load   e.g. () => import("../charts/ForecastChart.jsx")
 * @param {string} [exportName]          a named export of that module (default: "default")
 * @returns {() => import("react").LazyExoticComponent<any>} the current lazy component; call it
 *   during render so a replacement after a failure is picked up.
 */
export function retryingLazy(load, exportName = "default") {
  let attempt = 0;
  let current = null;
  const make = (failedUrl = null) => {
    attempt += 1;
    const n = attempt;
    const importer = failedUrl ? () => import(/* @vite-ignore */ `${failedUrl}?retry=${n}`) : load;
    current = lazy(() => importer()
      .then((module) => {
        const component = module?.[exportName];
        if (!component) throw new Error(`chart module has no ${exportName} export`);
        return { default: component };
      })
      .catch((error) => {
        make(failedModuleUrl(error) ?? (failedUrl && !String(error?.message).includes("export") ? failedUrl : null));
        throw error;
      }));
  };
  make();
  return () => current;
}
