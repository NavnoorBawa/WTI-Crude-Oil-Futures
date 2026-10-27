import { lazy } from "react";

// Chart modules load in their own chunks after the verdict above them has rendered. A failed chunk
// load (a flaky link, or a request racing a deploy) must not be final: React.lazy keeps a rejected
// import forever, so a fresh lazy component is swapped in, which the figure's error boundary
// renders when it resets on the next data refresh. Browsers also cache a failed module fetch, so
// the retry asks for the chunk under a new URL; Chromium and Firefox name it in the error,
// elsewhere the import is simply repeated.
const failedModuleUrl = (error) =>
  /\bhttps?:\/\/[^\s'"]+?\.js(?=$|[\s'"?#])/.exec(String(error?.message ?? ""))?.[0] ?? null;

// Every chart getter's preload (see preloadChunks).
const preloaders = [];

/**
 * @param {() => Promise<object>} load   e.g. () => import("../charts/ForecastChart.jsx")
 * @param {string} [exportName]          a named export of that module (default: "default")
 * @returns {(() => import("react").LazyExoticComponent<any>) & {preload: () => Promise<void>|null}}
 *   the current lazy component; call it during render so a replacement after a failure is picked
 *   up. Its `preload` fetches the chunk (preloadChunks calls every one).
 */
export function retryingLazy(load, exportName = "default") {
  let attempt = 0;
  let current = null;
  // The component, once its chunk has arrived (by a preload or a render).
  let loaded = null;
  let preloading = null;
  const pick = (module) => {
    const component = module?.[exportName];
    if (!component) throw new Error(`chart module has no ${exportName} export`);
    loaded = component;
    return component;
  };
  const make = (failedUrl = null) => {
    attempt += 1;
    const n = attempt;
    const importer = failedUrl ? () => import(/* @vite-ignore */ `${failedUrl}?retry=${n}`) : load;
    current = lazy(() => {
      // A chunk that has already arrived resolves at once (React reads a thenable that calls back
      // synchronously as resolved), so the first render does not suspend: the "beforeprint"
      // flushSync then prints the figure instead of its "Loading" placeholder.
      if (loaded) return { then: (resolve) => resolve({ default: loaded }) };
      return importer()
        .then((module) => ({ default: pick(module) }))
        .catch((error) => {
          make(failedModuleUrl(error) ?? (failedUrl && !String(error?.message).includes("export") ? failedUrl : null));
          throw error;
        });
    });
  };
  make();
  const get = () => current;
  get.preload = () => {
    // A failed preload is left to the render, which retries on its own.
    if (!loaded && !preloading) preloading = load().then((module) => { pick(module); }).catch(() => { preloading = null; });
    return preloading;
  };
  preloaders.push(get.preload);
  return get;
}

/**
 * Fetches every chart chunk not fetched yet, without rendering anything, so that a figure mounted
 * later (by the background queue, a scroll, or a plain Ctrl/Cmd+P's "beforeprint") renders at once.
 */
export const preloadChunks = () => preloaders.forEach((preload) => preload());
