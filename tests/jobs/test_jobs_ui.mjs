import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(new URL("../../job_platform/templates/jobs.html", import.meta.url), "utf8");
const script = await readFile(new URL("../../job_platform/static/jobs.js", import.meta.url), "utf8");
const registry = await readFile(new URL("../../job_platform/static/jobs-type-registry.js", import.meta.url), "utf8");

test("Jobs UI exposes accessible status and allowlisted type editor", () => {
  assert.match(template, /data-workspace-await-ready/);
  assert.match(template, /aria-live="polite"/);
  assert.match(template, /id="job-type-editor"/);
  assert.match(script, /workspaceAppReady/);
  assert.match(script, /editor\?\.destroy/);
  assert.match(registry, /new Map\(\[\["web-collection"/);
  assert.doesNotMatch(registry, /import\(.*key/);
});
