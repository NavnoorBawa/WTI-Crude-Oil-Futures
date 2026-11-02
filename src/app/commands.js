// The in-page moves the jump palette shares with the keyboard shortcuts and the code bar: the
// current section, jumps that land focus, the adjacent section, a section's link and a download.
// They touch the DOM and run only in the browser. What the palette lists is in ./paletteCommands.js.
import { SECTIONS } from "./sections";

// How long a revealed ledger row may take to appear before the jump settles for its section.
const REVEAL_WAIT_MS = 250;

// --------------------------------------------------------------------------- moving in the page
// The section the code bar marks as current (TopBar keeps it up to date), for the shortcuts.
let primarySection = null;
export const setPrimarySection = (id) => { primarySection = id ?? null; };
export const getPrimarySection = () => primarySection;

/** Tells the code bar which section the reader just moved to, before the scroll settles. */
export const pinSection = (id) => {
  if (id) window.dispatchEvent(new CustomEvent("nav:pin", { detail: { id } }));
};

const isShown = (element) => Boolean(element) && !element.closest("[hidden]");
const sectionOf = (element) => element?.closest("section.panel[id]") ?? null;
const headingIn = (element) => element?.querySelector("h2[tabindex]") ?? null;

/**
 * Focuses where a jump landed, without moving the view: a section's heading, or else the element
 * itself (a model or ledger row, a reference item), so a screen reader announces it and the next
 * Tab continues from there instead of from the section's top. An element that cannot take focus
 * gets tabindex -1 for as long as it has it.
 */
const focusTarget = (node) => {
  if (!node) return;
  if (node.matches("section")) {
    headingIn(node)?.focus({ preventScroll: true });
    return;
  }
  if (!node.hasAttribute("tabindex")) {
    node.setAttribute("tabindex", "-1");
    node.addEventListener("blur", () => node.removeAttribute("tabindex"), { once: true });
  }
  node.focus({ preventScroll: true });
};

/**
 * Scrolls a section (or an element in one) to the top of the view, under the bar, and focuses
 * the section's heading (or the element). Smooth through the page's CSS, instant under reduced motion.
 */
export function focusSection(element, { block = "start" } = {}) {
  if (!element) return;
  element.scrollIntoView({ block });
  pinSection((sectionOf(element) ?? element).id);
  focusTarget(element);
}

/**
 * Goes to an element by id, through the URL's #hash (so the browser, the hash landing, the method
 * notes and the ledger all react as they do to a link), then focuses it (a section, its heading). When
 * the element is missing or hidden it goes to `fallback` instead; with `reveal`, the hash is set
 * first and the fallback is used only if nothing showed the element within a moment (the ledger
 * shows a filtered-out row when the hash names it).
 *
 * @returns {boolean} whether there was anywhere to go
 */
export function jumpTo(target, { fallback = null, reveal = false } = {}) {
  const element = document.getElementById(target);
  const landOn = (id) => {
    const node = document.getElementById(id);
    if (!node) return false;
    if (window.location.hash === `#${id}`) {
      focusSection(node, { block: node.matches("section") ? "start" : "center" });
    } else {
      pinSection(sectionOf(node)?.id ?? id);
      window.location.hash = id;
      // The browser scrolls to it. A section's heading takes focus on the hashchange (useHashLanding).
      if (!node.matches("section")) focusTarget(node);
    }
    return true;
  };
  if (isShown(element)) return landOn(target);
  if (reveal && fallback) {
    pinSection(fallback);
    window.location.hash = target;
    window.setTimeout(() => {
      const shown = document.getElementById(target);
      if (isShown(shown)) {
        focusTarget(shown);
        return;
      }
      const url = `${window.location.pathname}${window.location.search}#${fallback}`;
      try { window.history.replaceState(window.history.state, "", url); } catch { /* sandboxed frame */ }
      focusSection(document.getElementById(fallback));
    }, REVEAL_WAIT_MS);
    return true;
  }
  return fallback ? landOn(fallback) : false;
}

/** The bar's height plus the scroll padding: where a section's top lands after a jump. */
const landingLine = () => {
  const padding = parseFloat(window.getComputedStyle(document.documentElement).scrollPaddingTop);
  return Number.isFinite(padding) ? padding : 0;
};

/**
 * The next (step 1) or previous (step -1) section from the current one, or, before any section is
 * current, the first one below the landing line (or the last one above it). Returns its id, or
 * null at either end.
 */
export function goToAdjacentSection(step) {
  const nodes = SECTIONS.map((section) => document.getElementById(section.id)).filter(isShown);
  if (!nodes.length) return null;
  const index = nodes.findIndex((node) => node.id === primarySection);
  const line = landingLine();
  let target;
  if (index >= 0) target = nodes[index + step] ?? null;
  else if (step > 0) target = nodes.find((node) => node.getBoundingClientRect().top > line + 1) ?? null;
  else target = [...nodes].reverse().find((node) => node.getBoundingClientRect().top < line - 1) ?? null;
  if (!target) return null;
  focusSection(target);
  return target.id;
}

/** A link to one section in the current view: the page's address, its view query and #id. */
export const sectionUrl = (id) => `${window.location.origin}${window.location.pathname}${window.location.search}${id ? `#${id}` : ""}`;

/** Saves a same-origin file under its own name (no new request beyond the file itself). */
export function downloadFile(href, name) {
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}
