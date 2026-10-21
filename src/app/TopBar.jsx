import { useEffect, useState } from "react";
import Chip from "../components/Chip";
import { fmtAge, fmtCT } from "../lib/format";
import useNow from "../lib/useNow";
import { REPO_URL, STALE_SNAPSHOT_MS } from "./config";
import { SECTIONS } from "./sections";

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

/** "Snapshot 2h 14m": how old the data is. Turns amber once a refresh is overdue. */
function SnapshotAge({ frozenAtMs, lastUpdate }) {
  const now = useNow(60 * 1000);
  if (frozenAtMs == null) {
    return <span className="snapshot-chip">{lastUpdate != null ? `Updated ${fmtAge(now - lastUpdate)} ago` : "Loading…"}</span>;
  }
  const age = now - frozenAtMs;
  const stale = age > STALE_SNAPSHOT_MS;
  return (
    <span className={`snapshot-chip${stale ? " is-stale" : ""}`} title={`Data frozen ${fmtCT(frozenAtMs)}`}>
      <span className="snapshot-dot" aria-hidden="true" />
      <span className="snapshot-word">Snapshot</span> <span className="num">{fmtAge(age)}</span>
      <span className="sr-only"> old, frozen {fmtCT(frozenAtMs)}</span>
    </span>
  );
}

/** Highlights the section code of whatever panel crosses the middle of the viewport. */
function useActiveSection(enabled) {
  const [active, setActive] = useState(null);
  useEffect(() => {
    if (!enabled || typeof IntersectionObserver === "undefined") return undefined;
    const nodes = SECTIONS.map((section) => document.getElementById(section.id)).filter(Boolean);
    if (!nodes.length) return undefined;
    // Track every section crossing the band at mid-viewport; the first in page order is active,
    // and none is when the band sits over the verdict or the footer.
    const inBand = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => (entry.isIntersecting ? inBand.add(entry.target.id) : inBand.delete(entry.target.id)));
      setActive(SECTIONS.find((section) => inBand.has(section.id))?.id ?? null);
    }, { rootMargin: "-45% 0px -50% 0px" });
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [enabled]);
  return active;
}

/**
 * The 48px top bar: brand, section-code navigation, snapshot age and the GitHub link.
 * @param {{frozenAtMs: number|null, lastUpdate: number|null, hasData: boolean}} props
 */
export default function TopBar({ frozenAtMs, lastUpdate, hasData }) {
  const active = useActiveSection(hasData);
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href="#top" aria-label="WTI Volatility Research, back to top">
          <BrandMark />
          <h1 className="brand-title">WTI Volatility Research</h1>
        </a>
        <nav className="codenav" aria-label="Sections">
          <ul>
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  title={section.name}
                  aria-label={`${section.code}: ${section.name}`}
                  aria-current={active === section.id ? "location" : undefined}
                >
                  {section.code}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="topbar-right">
          <SnapshotAge frozenAtMs={frozenAtMs} lastUpdate={lastUpdate} />
          <a className="topbar-link" href={REPO_URL} target="_blank" rel="noopener noreferrer" aria-label="Source on GitHub (opens in a new tab)">
            <Chip tone="muted">GitHub</Chip>
          </a>
        </div>
      </div>
    </header>
  );
}
