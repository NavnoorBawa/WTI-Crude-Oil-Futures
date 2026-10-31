#!/usr/bin/env node
// The dev-tooling audit with dated exceptions (CLAUDE.md: pin only releases at least 14 days old).
//
// When an advisory lands in build tooling whose only fix is younger than the pinning policy allows,
// .github/audit-exceptions.json can list it: the advisory, the package it is reported in, the date
// the exception ends and why. This script reads an `npm audit --json` report (on standard input with
// the argument `-`, or it runs `npm audit --json` itself) and exits 0 only when every advisory in it has an exception that
// names that advisory and package and has not ended; otherwise it prints what is not excused and
// exits 1. Packages that are flagged only because they depend on a vulnerable one (string entries
// in `via`) add no advisory of their own. The production audit (`npm audit --omit=dev`) never reads
// this file: it stays strict. No dependencies.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const EXCEPTIONS = new URL("../.github/audit-exceptions.json", import.meta.url);

/** Every advisory a report names, with the package it is reported in: [{ advisory, package, url, severity }]. */
export function advisories(report) {
  if (!report || typeof report !== "object" || report.error) {
    throw new Error(`not an npm audit report${report?.error ? `: ${JSON.stringify(report.error)}` : ""}`);
  }
  const found = [];
  for (const entry of Object.values(report.vulnerabilities ?? {})) {
    for (const via of entry.via ?? []) {
      if (via && typeof via === "object") {
        const url = String(via.url ?? "");
        found.push({ advisory: url.split("/").pop() || String(via.source), package: via.name, url, severity: via.severity });
      }
    }
  }
  return found;
}

/** The advisories no current exception covers. `today` is an ISO date (YYYY-MM-DD); an exception covers dates before its `expires`. */
export function unexcused(report, exceptions, today) {
  return advisories(report).filter((found) => !exceptions.some((exception) =>
    exception.advisory === found.advisory && exception.package === found.package && today < exception.expires));
}

function main(argv) {
  const { exceptions } = JSON.parse(readFileSync(EXCEPTIONS, "utf8"));
  let text;
  if (argv[0] === "-") {
    text = readFileSync(0, "utf8");                  // the report on standard input
  } else if (argv.length) {
    console.error("usage: audit_exceptions.mjs [-]   (- reads an npm audit --json report from standard input)");
    return 2;
  } else {
    // npm audit exits 1 when it finds anything; its JSON report is on stdout either way.
    text = spawnSync("npm", ["audit", "--json"], { encoding: "utf8", shell: process.platform === "win32" }).stdout;
  }
  const today = new Date().toISOString().slice(0, 10);
  const report = JSON.parse(text);
  const open = new Set(unexcused(report, exceptions, today).map((f) => `${f.advisory} ${f.package}`));
  for (const found of advisories(report)) {
    const exception = exceptions.find((e) => e.advisory === found.advisory && e.package === found.package);
    const status = !open.has(`${found.advisory} ${found.package}`) ? `excused until ${exception.expires}`
      : exception ? `exception ended ${exception.expires}` : "no exception";
    console.log(`${found.advisory} (${found.package}, ${found.severity}): ${status}`);
  }
  if (open.size) {
    console.error(`${open.size} advisor${open.size === 1 ? "y is" : "ies are"} not excused: ${[...open].join(", ")}`);
    return 1;
  }
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exit(main(process.argv.slice(2)));
