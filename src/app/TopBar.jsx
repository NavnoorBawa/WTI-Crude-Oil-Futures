import { useCallback, useEffect, useRef, useState } from "react";
import Chip from "../components/Chip";
import CommandPalette from "../components/CommandPalette";
import { copyText } from "../lib/clipboard";
import { fmtAge, fmtCT } from "../lib/format";
import useNow from "../lib/useNow";
import { getPrimarySection, goToAdjacentSection, pinSection, sectionUrl, setPrimarySection } from "./commands";
import { REPO_URL, STALE_SNAPSHOT_MS } from "./config";
import { SECTIONS, sectionById, startsGroup } from "./sections";
import useShortcuts, { isTypingTarget, useSingleKeys } from "./useShortcuts";
import { parseViewList, parseViewParams, useViewList } from "./viewState";

const EMPTY = new Set();
// Scrolling has settled once no scroll event arrives for this long; only then can a pinned
// section lose its place to the band (a smooth scroll crosses every section in between).
const SETTLE_MS = 160;
const INTENT_EVENTS = ["wheel", "touchstart", "mousedown", "keydown"]; // mousedown: a scrollbar drag
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
const TOAST_MS = 2400;

const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/** The brand mark: a forecast line inside its interval band. Decorative. */
function BrandMark() {
  return (
    <svg className="brand-mark" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="19" height="19" rx="2" className="brand-mark-frame" />
      <path d="M3 11 L7 8 L10 9.5 L14 5 L17 6.5 L17 13.5 L14 12 L10 15 L7 13.5 L3 15 Z" className="brand-mark-band" />
      <path d="M3 13 L7 10.8 L10 12.2 L14 8.6 L17 10" className="brand-mark-line" />
    </svg>
  );
}

/**
 * "Snapshot 2h 14m" (phones: "2h old"): how old the data is. Turns amber once a refresh is overdue.
 * Screen readers get one full sentence instead of the visible fragments.
 */
function SnapshotAge({ frozenAtMs, lastUpdate }) {
  const now = useNow(60 * 1000);
  if (frozenAtMs == null) {
    // The clock ticks once a minute, so a fetch can be newer than `now`.
    const since = lastUpdate != null ? Math.max(0, now - lastUpdate) : null;
    const text = since == null ? "Loading…" : since < 60 * 1000 ? "Updated just now" : `Updated ${fmtAge(since)} ago`;
    return <span className="snapshot-chip">{text}</span>;
  }
  // A client clock behind the server's would make the age negative: it reads 0m.
  const age = fmtAge(Math.max(0, now - frozenAtMs));
  const stale = now - frozenAtMs > STALE_SNAPSHOT_MS;
  const [lead, ...rest] = age.split(" ");
  return (
    <span className={`snapshot-chip${stale ? " is-stale" : ""}`} title={`Data frozen ${fmtCT(frozenAtMs)}`}>
      <span className="snapshot-dot" aria-hidden="true" />
      <span aria-hidden="true">
        <span className="snapshot-word">Snapshot </span>
        <span className="num">{lead}</span>
        {rest.length > 0 && <span className="num snapshot-rest"> {rest.join(" ")}</span>}
        <span className="snapshot-old"> old</span>
      </span>
      <span className="sr-only">Snapshot {age} old, frozen {fmtCT(frozenAtMs)}</span>
    </span>
  );
}

const decode = (text) => {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
};

/**
 * Which sections cross the band at mid-viewport (inBand: both halves of a paired row) and which
 * one is current (primary): the first in page order, or the section the reader just moved to by
 * the bar, a link, the palette or j/k. That one stays current through its scroll and until a later
 * band change leaves it out. New payloads (data, research) may render the sections anew, so the
 * observer then starts over on the elements actually on the page.
 */
function useActiveSection(enabled, data, research) {
  const [state, setState] = useState({ inBand: EMPTY, primary: null });
  const moved = useRef(false); // the reader has scrolled, jumped or followed a link since loading
  useEffect(() => {
    if (!enabled || typeof IntersectionObserver === "undefined") return undefined;
    const nodes = SECTIONS.map((section) => document.getElementById(section.id)).filter(Boolean);
    if (!nodes.length) return undefined;
    const inBand = new Set();
    let pin = null;
    let settle = null;
    const publish = () => {
      const first = SECTIONS.find((section) => inBand.has(section.id))?.id ?? null;
      setState({ inBand: new Set(inBand), primary: pin?.id ?? first });
    };
    const arm = () => { if (pin) pin.armed = true; };
    const waitForSettle = () => {
      clearTimeout(settle);
      settle = setTimeout(arm, SETTLE_MS);
    };
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => (entry.isIntersecting ? inBand.add(entry.target.id) : inBand.delete(entry.target.id)));
      if (pin?.armed && !inBand.has(pin.id)) pin = null;
      publish();
    }, { rootMargin: "-45% 0px -50% 0px" });
    nodes.forEach((node) => observer.observe(node));

    const pinTo = (id) => {
      if (!sectionById(id)) return;
      pin = { id, armed: false };
      waitForSettle();
      publish();
    };
    const pinHash = () => {
      const target = document.getElementById(decode(window.location.hash.slice(1)));
      const section = target?.closest("section.panel[id]");
      if (section) pinTo(section.id);
    };
    const onPin = (event) => {
      moved.current = true;
      pinTo(event.detail?.id);
    };
    const onHash = () => {
      moved.current = true;
      pinHash();
    };
    const onScroll = () => { if (pin && !pin.armed) waitForSettle(); };
    // The reader scrolling on their own takes over at once, even mid-way through a smooth scroll.
    const onIntent = (event) => {
      if (event.type === "keydown" && (!SCROLL_KEYS.has(event.key) || isTypingTarget(event.target))) return;
      moved.current = true;
      arm();
    };
    // A cold deep link (#vrp) lands without a hashchange: the section it names is current, until
    // the reader moves (after that an old #hash says nothing about where they are).
    if (!moved.current) pinHash();
    window.addEventListener("nav:pin", onPin);
    window.addEventListener("hashchange", onHash);
    window.addEventListener("scroll", onScroll, { passive: true });
    INTENT_EVENTS.forEach((name) => window.addEventListener(name, onIntent, { passive: true }));
    return () => {
      observer.disconnect();
      clearTimeout(settle);
      window.removeEventListener("nav:pin", onPin);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("scroll", onScroll);
      INTENT_EVENTS.forEach((name) => window.removeEventListener(name, onIntent));
    };
  }, [enabled, data, research]);
  return state;
}

/**
 * Writes the bar's rendered height to --topbar-total on :root (0 while it does not stick), so the
 * html scroll-padding clears it at every width, zoom level and wrap.
 */
function useBarHeight(ref) {
  useEffect(() => {
    const bar = ref.current;
    if (!bar) return undefined;
    const root = document.documentElement;
    const update = () => {
      const sticky = window.getComputedStyle(bar).position === "sticky";
      root.style.setProperty("--topbar-total", `${sticky ? Math.ceil(bar.getBoundingClientRect().height) : 0}px`);
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(bar);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [ref]);
}

/**
 * On a strip that scrolls sideways (phones): keeps the current code in the middle of the strip
 * (scrolling the strip only, never the page) and marks which edges have more codes beyond them.
 */
function useStripScroll(ref, ready, primary) {
  useEffect(() => {
    const nav = ref.current;
    if (!ready || !nav) return undefined;
    const update = () => {
      const max = nav.scrollWidth - nav.clientWidth;
      nav.toggleAttribute("data-more-left", max > 1 && nav.scrollLeft > 1);
      nav.toggleAttribute("data-more-right", max > 1 && nav.scrollLeft < max - 1);
    };
    update();
    // The strip's box and the list's (codes widen when the web font arrives).
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(nav);
    if (nav.firstElementChild) observer?.observe(nav.firstElementChild);
    nav.addEventListener("scroll", update, { passive: true });
    return () => {
      observer?.disconnect();
      nav.removeEventListener("scroll", update);
    };
  }, [ref, ready]);

  useEffect(() => {
    const nav = ref.current;
    if (!nav || !primary || nav.scrollWidth <= nav.clientWidth + 1) return;
    const link = nav.querySelector(`a[href="#${primary}"]`);
    if (!link) return;
    // From the boxes, not offsetLeft: a group's first item is positioned (its divider), so it would
    // be the link's offset parent.
    const box = link.getBoundingClientRect();
    const strip = nav.getBoundingClientRect();
    const left = nav.scrollLeft + box.left - strip.left - (nav.clientWidth - box.width) / 2;
    nav.scrollTo({ left: Math.max(0, left), behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [ref, primary]);
}

/**
 * A short confirmation (copied, opened, nothing to toggle), shown and announced. The same message
 * twice in a row still changes the live region's text (a trailing no-break space comes and goes),
 * so a screen reader repeats it.
 */
function useToast() {
  const [message, setMessage] = useState({ text: "", count: 0 });
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const notify = useCallback((text) => {
    clearTimeout(timer.current);
    setMessage((current) => ({ text: `${text}${current.count % 2 ? "\u00a0" : ""}`, count: current.count + 1 }));
    timer.current = setTimeout(() => setMessage((current) => ({ ...current, text: "" })), TOAST_MS);
  }, []);
  return [message.text, notify];
}

/**
 * The top bar: brand (and, on wide screens, the current section's name), the section codes in
 * four groups, the snapshot age, the Jump button that opens the palette, and the GitHub link.
 * Until the dashboard is on screen (loading, or an error screen) the codes and the Jump button
 * keep their space but are not shown, so nothing links to a section that does not exist.
 *
 * @param {{frozenAtMs: number|null, lastUpdate: number|null, hasData: boolean, data?: object|null,
 *   research?: object|null}} props
 */
export default function TopBar({ frozenAtMs, lastUpdate, hasData, data = null, research = null }) {
  const ready = Boolean(hasData) && !data?.error;
  const barRef = useRef(null);
  const navRef = useRef(null);
  const openerRef = useRef(null);
  const { inBand, primary } = useActiveSection(ready, data, research);
  const [palette, setPalette] = useState(null); // null | {view, nonce}
  const [toast, notify] = useToast();
  const [, toggleTable] = useViewList("table");
  const singleKeys = useSingleKeys();
  useBarHeight(barRef);
  useStripScroll(navRef, ready, primary);
  useEffect(() => { setPrimarySection(primary); }, [primary]);

  const openPalette = useCallback((view = "search") => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPalette((current) => ({ view, nonce: (current?.nonce ?? 0) + 1 }));
  }, []);

  // Escape, the close button or the backdrop: focus goes back to where it was. A jump has already
  // moved focus to its destination.
  const closePalette = useCallback(({ restore }) => {
    const opener = openerRef.current;
    openerRef.current = null;
    setPalette(null);
    if (restore && opener?.isConnected && opener !== document.body) opener.focus({ preventScroll: true });
  }, []);

  const onShortcut = useCallback((action) => {
    const current = getPrimarySection();
    const section = sectionById(current);
    switch (action) {
      case "palette": openPalette("search"); break;
      case "shortcuts": openPalette("shortcuts"); break;
      case "next": if (!goToAdjacentSection(1)) notify(current ? "This is the last section" : "No section below"); break;
      case "previous": if (!goToAdjacentSection(-1)) notify(current ? "This is the first section" : "No section above"); break;
      case "table": {
        const toggle = section ? document.querySelector(`#${section.id} button[aria-controls="${section.id}-body"]`) : null;
        if (!section) notify("Scroll to a section first");
        else if (!toggle) notify(`${section.code} has no table view`);
        else {
          const on = parseViewList(parseViewParams(window.location.search).table).includes(section.id);
          toggleTable(section.id);
          notify(`${section.code} table view ${on ? "off" : "on"}`);
        }
        break;
      }
      case "copy-link":
        copyText(sectionUrl(section?.id ?? null)).then((ok) => notify(ok
          ? (section ? `Link to ${section.code} copied` : "Link to this view copied")
          : "Could not copy the link"));
        break;
      default:
        break;
    }
  }, [notify, openPalette, toggleTable]);
  useShortcuts(ready, onShortcut);

  const current = sectionById(primary);
  return (
    <header className="topbar" ref={barRef}>
      <div className="topbar-inner">
        <a className="brand" href="#top" aria-label="WTI Volatility Research, back to top">
          <BrandMark />
          <h1 className="brand-title">WTI Volatility Research</h1>
        </a>
        <span className="brand-section" aria-hidden="true">{ready && current ? `/ ${current.name}` : ""}</span>
        {ready ? (
          <nav className="codenav" aria-label="Sections" ref={navRef}>
            <ul>
              {SECTIONS.map((section, index) => (
                <li key={section.id} className={startsGroup(index) ? "group-start" : undefined}>
                  <a
                    href={`#${section.id}`}
                    title={section.name}
                    aria-label={`${section.code}: ${section.name}`}
                    aria-current={primary === section.id ? "location" : undefined}
                    className={inBand.has(section.id) ? "is-inview" : undefined}
                    onClick={() => pinSection(section.id)}
                  >
                    {section.code}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        ) : <div className="codenav is-reserved" aria-hidden="true" />}
        <div className="topbar-right">
          <SnapshotAge frozenAtMs={frozenAtMs} lastUpdate={lastUpdate} />
          <button
            type="button"
            className={`jump-button${ready ? "" : " is-reserved"}`}
            aria-label="Jump: search sections, models, hypotheses and terms"
            aria-haspopup="dialog"
            aria-keyshortcuts={singleKeys ? "/ Control+K Meta+K" : "Control+K Meta+K"}
            disabled={!ready}
            onClick={() => openPalette("search")}
          >
            Jump{singleKeys && <kbd className="jump-key" aria-hidden="true">/</kbd>}
          </button>
          <a className="topbar-link" href={REPO_URL} target="_blank" rel="noopener noreferrer" aria-label="Source on GitHub (opens in a new tab)">
            <Chip tone="muted">GitHub</Chip>
          </a>
        </div>
      </div>
      {palette && ready && (
        <CommandPalette
          key={palette.nonce}
          initialView={palette.view}
          data={data}
          research={research}
          onClose={closePalette}
          notify={notify}
        />
      )}
      <p className="nav-toast" role="status" data-shown={toast ? "" : undefined}>{toast}</p>
    </header>
  );
}
