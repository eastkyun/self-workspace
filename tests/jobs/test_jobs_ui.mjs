import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createJobsApi } from "../../job_platform/static/jobs-api.js";

const template = await readFile(new URL("../../job_platform/templates/jobs.html", import.meta.url), "utf8");
const script = await readFile(new URL("../../job_platform/static/jobs.js", import.meta.url), "utf8");
const registry = await readFile(new URL("../../job_platform/static/jobs-type-registry.js", import.meta.url), "utf8");
const editor = await readFile(new URL("../../web_collector/static/web-collection-editor.js", import.meta.url), "utf8");
const styles = await readFile(new URL("../../job_platform/static/jobs.css", import.meta.url), "utf8");
const jobsIcon = await readFile(new URL("../../portal/static/icons/jobs.svg", import.meta.url), "utf8");

test("Jobs UI exposes accessible status and allowlisted type editor", () => {
  assert.match(template, /data-workspace-await-ready/);
  assert.match(template, /aria-live="polite"/);
  assert.match(template, /id="job-type-editor"/);
  assert.match(script, /workspaceAppReady/);
  assert.match(script, /editor\?\.destroy/);
  assert.match(registry, /new Map\(\[\["web-collection"/);
  assert.doesNotMatch(registry, /import\(.*key/);
  assert.match(editor, /페이지 미리보기/);
  assert.match(editor, /<dialog class="web-preview-dialog"/);
  assert.match(editor, /web-preview-target/);
  assert.match(editor, /previewDialog\.showModal\(\)/);
  assert.match(editor, /previewDialog\.close\(\)/);
  assert.match(editor, /requestSequence !== sequence/);
  assert.match(editor, /web-preview-loading/);
  assert.match(editor, /retry\.textContent = "다시 시도"/);
  assert.match(editor, /retry\.addEventListener\("click", loadPreview\)/);
  assert.match(editor, /previewInFlight/);
  assert.match(editor, /requestAnimationFrame\(\(\) => retry\.focus\(\)\)/);
  assert.match(editor, /web-preview-stage/);
  assert.match(editor, /Note 정리 방식/);
  assert.match(editor, /FINANCIAL_MARKET_BRIEFING/);
  assert.match(editor, /OPENAI_API_KEY/);
  assert.match(editor, /output_format: outputFormat\.value === "RAW_MARKDOWN"/);
  assert.match(editor, /formatter_profile_id: formatterProfileId/);
  assert.match(editor, /setFormatters/);
  assert.match(editor, /profile\.is_builtin/);
  assert.match(editor, /previewButton\.setAttribute\("aria-busy", "true"\)/);
  assert.match(editor, /aria-pressed/);
  assert.doesNotMatch(editor, /innerHTML\s*=\s*value\./);
  assert.match(script, /테스트/);
  assert.match(script, /활성화/);
  assert.match(script, /일시정지/);
  assert.match(script, /지금 실행/);
  assert.match(script, /실행 이력/);
  assert.match(script, /Job 설정 편집/);
  assert.match(script, /api\.get\(job\.id\)/);
  assert.match(script, /editor\.hydrate\(value\)/);
  assert.match(script, /expected_revision: editingJob\.definition_revision/);
  assert.match(script, /api\[confirmationAction\]\(targetJob\.id\)/);
  assert.match(script, /영구 삭제/);
  assert.match(script, /confirmationAction === "remove"/);
  assert.match(script, /api\.removeRun\(targetJob\.id, targetRun\.id\)/);
  assert.match(script, /실행 기록을 삭제할까요/);
  assert.match(styles, /\.job-run-delete/);
  assert.match(script, /followRun/);
  assert.match(script, /Worker 처리를 기다리고 있습니다/);
  assert.match(script, /설정 테스트를 백그라운드에 등록했습니다/);
  assert.match(script, /startsWith\("test:"\)/);
  assert.match(script, /설정 테스트 ·/);
  assert.match(script, /followRun\(job\.id, result\.id, cardStatus, \{ test: true \}\)/);
  assert.match(script, /!testRun && \["FAILED", "PARTIAL"\]/);
  assert.match(script, /const disabledStates = new Map/);
  assert.match(script, /BROWSER_SATURATED: "다른 미리보기나 수집이 진행 중입니다/);
  assert.match(script, /BROWSER_NAVIGATION_FAILED: "페이지 연결이나 렌더링이 중간에 끊겼습니다/);
  assert.match(script, /AI_FORMATTER_UNAVAILABLE/);
  assert.match(script, /trigger\.textContent = "실행 중…"/);
  assert.match(script, /item\.disabled = disabledStates\.get\(item\)/);
  assert.match(script, /Note가 생성되었습니다/);
  assert.match(script, /api\.retry\(job\.id, run\.id\)/);
  assert.match(script, /재시도가 대기열에 등록되었습니다/);
  assert.match(script, /note_id=/);
  assert.match(template, /class="jobs-overview"/);
  assert.match(template, /class="jobs-form-scroll"/);
  assert.match(template, /<dialog id="jobs-create-dialog"/);
  assert.match(template, /class="jobs-form-progress"/);
  assert.match(template, /id="jobs-archive-dialog"/);
  assert.match(template, /id="jobs-history-dialog"/);
  assert.match(template, /id="jobs-formatter-dialog"/);
  assert.match(template, /정리 방식 관리/);
  assert.match(template, /실행 이력은 유지됩니다/);
  assert.match(template, /data-action="cancel-form"/);
  assert.match(script, /jobs-count-active/);
  assert.match(script, /api\.health\(\)/);
  assert.match(script, /jobs-worker-state/);
  assert.match(script, /jobs-empty-mark/);
  assert.match(styles, /\.jobs-create-dialog::backdrop/);
  assert.match(styles, /\.jobs-confirm-dialog::backdrop/);
  assert.match(styles, /\.jobs-history-dialog::backdrop/);
  assert.match(styles, /\.jobs-formatter-dialog::backdrop/);
  assert.match(styles, /\.web-preview-dialog::backdrop/);
  assert.match(styles, /\.web-preview-dialog \{[^}]*resize: both/);
  assert.match(styles, /\.web-preview-canvas \{[^}]*overflow: auto/);
  assert.match(styles, /\.web-preview-stage \{[^}]*position: relative/);
  assert.match(styles, /@keyframes jobs-preview-spin/);
  assert.match(styles, /\.web-preview-error \.web-preview-retry/);
  assert.match(styles, /resize: both/);
  assert.match(styles, /\.jobs-form-scroll \{[^}]*overflow-y: auto/);
  assert.match(styles, /@media \(max-width: 760px\)/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(jobsIcon, /fill="#e7f7f2"/);
  assert.match(jobsIcon, /stroke="#0b8064"/);
  assert.doesNotMatch(jobsIcon, /currentColor/);
  assert.match(script, /createDialog\.showModal\(\)/);
  assert.match(script, /createDialog\.close\(\)/);
  assert.match(script, /historyDialog\.showModal\(\)/);
  assert.match(script, /historyDialog\.open/);
  assert.match(script, /renderRuns\(historyList, await api\.runs/);
  assert.match(script, /api\.createFormatter/);
  assert.match(script, /api\.updateFormatter/);
  assert.match(script, /api\.removeFormatter/);
  assert.match(script, /formatterDeleteArmed/);
  assert.match(script, /기본값 복원/);
  assert.match(script, /profile\.is_builtin/);
  assert.match(script, /attempt < 160/);
  assert.doesNotMatch(script, /runs\.hidden/);
});

test("Jobs API sends CSRF-protected management actions and loads history", async () => {
  const calls = [];
  const fakeFetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({}) };
  };
  const api = createJobsApi("/api/jobs", fakeFetch, () => "csrf-token");
  await api.get("job/1");
  await api.formatters();
  await api.getFormatter("profile/1");
  await api.createFormatter({ name: "brief" });
  await api.updateFormatter("profile/1", { expected_revision: 1 });
  await api.removeFormatter("profile/1");
  await api.update("job/1", { expected_revision: 1 });
  await api.archive("job/1");
  await api.remove("job/1");
  await api.test("job/1");
  await api.activate("job/1");
  await api.pause("job/1");
  await api.run("job/1");
  await api.runs("job/1");
  await api.removeRun("job/1", "run/1");
  await api.retry("job/1", "run/1");

  assert.deepEqual(calls.map((call) => call.url), [
    "/api/jobs/job%2F1",
    "/api/jobs/formatters",
    "/api/jobs/formatters/profile%2F1",
    "/api/jobs/formatters",
    "/api/jobs/formatters/profile%2F1",
    "/api/jobs/formatters/profile%2F1",
    "/api/jobs/job%2F1",
    "/api/jobs/job%2F1",
    "/api/jobs/job%2F1/permanent",
    "/api/jobs/job%2F1/test",
    "/api/jobs/job%2F1/activate",
    "/api/jobs/job%2F1/pause",
    "/api/jobs/job%2F1/run",
    "/api/jobs/job%2F1/runs",
    "/api/jobs/job%2F1/runs/run%2F1",
    "/api/jobs/job%2F1/runs/run%2F1/retry",
  ]);
  assert.equal(calls[0].options.headers["X-CSRF-Token"], undefined);
  assert.equal(calls[1].options.headers["X-CSRF-Token"], undefined);
  assert.equal(calls[2].options.headers["X-CSRF-Token"], undefined);
  for (const index of [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15]) {
    assert.equal(calls[index].options.headers["X-CSRF-Token"], "csrf-token");
  }
  assert.equal(calls[13].options.headers["X-CSRF-Token"], undefined);
  assert.equal(calls[4].options.method, "PUT");
  assert.equal(calls[5].options.method, "DELETE");
  assert.equal(calls[6].options.method, "PUT");
  assert.equal(calls[8].options.method, "DELETE");
  assert.equal(calls[14].options.method, "DELETE");
});
