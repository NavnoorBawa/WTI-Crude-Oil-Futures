// The dev-tooling audit's dated exceptions (scripts/audit_exceptions.mjs, .github/audit-exceptions.json):
// an advisory is excused only by an exception that names it and the package it is reported in, and
// only before the exception's end date; packages flagged only through a dependency add nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { advisories, unexcused } from "../../scripts/audit_exceptions.mjs";

// The shape `npm audit --json` (report version 2) gave for GHSA-g3hv-75wg-m249, trimmed.
const report = {
  auditReportVersion: 2,
  vulnerabilities: {
    eslint: { name: "eslint", severity: "moderate", isDirect: true, via: ["@eslint/config-array", "minimatch"] },
    "@eslint/config-array": { name: "@eslint/config-array", severity: "moderate", isDirect: false, via: ["minimatch"] },
    minimatch: {
      name: "minimatch", severity: "moderate", isDirect: false,
      via: [{ source: 1244012, name: "minimatch", url: "https://github.com/advisories/GHSA-g3hv-75wg-m249",
        range: "<11.0.1", severity: "moderate" }],
    },
  },
  metadata: { vulnerabilities: { moderate: 3, total: 3 } },
};
const exception = { advisory: "GHSA-g3hv-75wg-m249", package: "minimatch", expires: "2026-11-10", reason: "test" };

test("only an advisory object counts; packages flagged through a dependency add none", () => {
  assert.deepEqual(advisories(report), [{ advisory: "GHSA-g3hv-75wg-m249", package: "minimatch",
    url: "https://github.com/advisories/GHSA-g3hv-75wg-m249", severity: "moderate" }]);
  assert.deepEqual(advisories({ auditReportVersion: 2, vulnerabilities: {} }), []);
});

test("an exception covers its advisory in its package until the day it expires", () => {
  assert.deepEqual(unexcused(report, [exception], "2026-10-31"), []);
  assert.deepEqual(unexcused(report, [exception], "2026-11-09"), []);
  assert.equal(unexcused(report, [exception], "2026-11-10").length, 1);            // the end date no longer applies
  assert.equal(unexcused(report, [{ ...exception, package: "eslint" }], "2026-10-31").length, 1);
  assert.equal(unexcused(report, [{ ...exception, advisory: "GHSA-0000-0000-0000" }], "2026-10-31").length, 1);
  assert.equal(unexcused(report, [], "2026-10-31").length, 1);
});

test("a report npm could not produce is an error, not a clean audit", () => {
  assert.throws(() => advisories({ error: { code: "ENOTFOUND" } }), /not an npm audit report/);
  assert.throws(() => advisories(null), /not an npm audit report/);
});

test("every exception names one advisory and package, ends on a real date and says why", () => {
  const { exceptions } = JSON.parse(readFileSync(new URL("../../.github/audit-exceptions.json", import.meta.url), "utf8"));
  const seen = new Set();
  for (const entry of exceptions) {
    assert.match(entry.advisory, /^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$/);
    assert.match(entry.package, /^(@[\w.-]+\/)?[\w.-]+$/);
    assert.match(entry.expires, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(new Date(`${entry.expires}T00:00:00Z`).toISOString().slice(0, 10), entry.expires);
    assert.ok(entry.reason.length > 40, "an exception says why it is safe and what ends it");
    const key = `${entry.advisory} ${entry.package}`;
    assert.ok(!seen.has(key), `duplicate exception ${key}`);
    seen.add(key);
  }
});
