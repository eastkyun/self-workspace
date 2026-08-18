import { createJobsApi } from "./jobs-api.js";
import { mountJobTypeEditor } from "./jobs-type-registry.js";

const root = document.querySelector("#jobs-app");
if (root) {
  const csrfToken = () => {
    const prefix = `${root.dataset.csrfCookieName}=`;
    const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
    return item ? decodeURIComponent(item.slice(prefix.length)) : "";
  };
  const api = createJobsApi(root.dataset.apiBase, fetch, csrfToken);
  const list = root.querySelector("#jobs-list");
  const status = root.querySelector("#jobs-status");
  const createDialog = root.querySelector("#jobs-create-dialog");
  const archiveDialog = root.querySelector("#jobs-archive-dialog");
  const historyDialog = root.querySelector("#jobs-history-dialog");
  const formatterDialog = root.querySelector("#jobs-formatter-dialog");
  const formatterList = root.querySelector("#jobs-formatter-list");
  const formatterForm = root.querySelector("#jobs-formatter-form");
  const formatterName = root.querySelector("#jobs-formatter-name");
  const formatterModel = root.querySelector("#jobs-formatter-model");
  const formatterInstructions = root.querySelector("#jobs-formatter-instructions");
  const formatterStatus = root.querySelector("#jobs-formatter-status");
  const formatterDelete = root.querySelector("#jobs-formatter-delete");
  const historyTitle = root.querySelector("#jobs-history-title");
  const historyStatus = root.querySelector("#jobs-history-status");
  const historyList = root.querySelector("#jobs-history-list");
  const form = root.querySelector("#jobs-form");
  const formStatus = root.querySelector("#job-form-status");
  const typesHost = root.querySelector("#job-types");
  const kind = root.querySelector("#job-schedule-kind");
  const expressionField = root.querySelector("#job-expression-field");
  const timezoneField = root.querySelector("#job-timezone-field");
  const nameField = root.querySelector("#job-name");
  const expressionInput = root.querySelector("#job-schedule-expression");
  const timezoneInput = root.querySelector("#job-timezone");
  const submitButton = root.querySelector("#job-submit");
  const formTitle = root.querySelector("#job-form-title");
  const formEyebrow = root.querySelector("#job-form-eyebrow");
  const formDescription = root.querySelector("#job-form-description");
  const formHint = root.querySelector("#job-form-hint");
  const typeLockNote = root.querySelector("#job-type-lock-note");
  const statusLabels = { DRAFT: "초안", ACTIVE: "활성", PAUSED: "일시정지", ARCHIVED: "보관됨" };
  const runLabels = { QUEUED: "대기", RUNNING: "실행 중", SUCCEEDED: "성공", UNCHANGED: "변경 없음", PARTIAL: "일부 실패", FAILED: "실패", CANCELLED: "취소" };
  const operationMessages = {
    BROWSER_SATURATED: "다른 미리보기나 수집이 진행 중입니다. 잠시 기다린 뒤 다시 시도하세요.",
    BROWSER_TIMEOUT: "페이지가 제한 시간 안에 안정되지 않았습니다.",
    BROWSER_NAVIGATION_FAILED: "페이지 연결이나 렌더링이 중간에 끊겼습니다. 잠시 뒤 다시 테스트하세요.",
    BROWSER_CRASHED: "브라우저가 중간에 종료되었습니다. Worker가 복구된 뒤 다시 시도하세요.",
    TRANSFER_LIMIT_EXCEEDED: "페이지가 허용된 전송량을 초과했습니다.",
    SELECTOR_NOT_FOUND: "선택한 수집 영역을 현재 페이지에서 찾지 못했습니다.",
    SELECTOR_NOT_UNIQUE: "선택한 영역이 여러 곳과 일치합니다. 미리보기에서 다시 선택하세요.",
    SELECTOR_VERIFIED: "현재 설정 테스트에 성공했습니다.",
    AI_FORMATTER_UNAVAILABLE: "AI 금융시장 브리핑을 사용하려면 Worker에 OPENAI_API_KEY를 설정하세요.",
    AI_FORMATTER_AUTH_FAILED: "OpenAI API 인증에 실패했습니다. Worker의 API 키를 확인하세요.",
    AI_FORMATTER_RATE_LIMITED: "OpenAI API 사용 한도에 도달했습니다. 잠시 뒤 다시 시도하세요.",
    AI_FORMATTER_TIMEOUT: "금융시장 브리핑 정리가 제한 시간 안에 끝나지 않았습니다.",
    AI_FORMATTER_INVALID_OUTPUT: "금융시장 브리핑 결과 형식이 올바르지 않습니다.",
    AI_FORMATTER_INVALID_CONFIG: "선택한 정리 방식의 지침이나 모델 설정을 확인하세요.",
    AI_FORMATTER_PROFILE_NOT_FOUND: "선택한 정리 방식을 찾을 수 없습니다. Job 설정을 다시 저장하세요.",
    AI_FORMATTER_FAILED: "금융시장 브리핑을 정리하지 못했습니다.",
  };
  let selectedType = null;
  let editor = null;
  let jobTypes = [];
  let editingJob = null;
  let formReturnFocus = null;
  let archiveTarget = null;
  let runDeleteTarget = null;
  let archiveReturnFocus = null;
  let confirmationAction = "archive";
  let historyTargetJob = null;
  let historyReturnFocus = null;
  let formatterProfiles = [];
  let editingFormatter = null;
  let formatterReturnFocus = null;
  let formatterDeleteArmed = false;

  function resetEditor() {
    editor?.destroy();
    editor = null;
    selectedType = null;
    [...typesHost.children].forEach((item) => {
      item.disabled = false;
      item.setAttribute("aria-pressed", "false");
    });
  }

  function resetForm() {
    form.reset();
    editingJob = null;
    resetEditor();
    renderSchedule();
    formStatus.textContent = "";
    formTitle.textContent = "새 Job 만들기";
    formEyebrow.textContent = "NEW AUTOMATION";
    formDescription.textContent = "수집 대상과 실행 일정을 한 번에 설정합니다.";
    formHint.textContent = "저장 후 테스트를 통과하면 활성화할 수 있습니다.";
    submitButton.textContent = "초안 저장";
    typeLockNote.hidden = true;
  }

  function closeForm() {
    if (createDialog.open) createDialog.close();
  }

  function showForm(returnFocus) {
    formReturnFocus = returnFocus || document.activeElement;
    if (!createDialog.open) createDialog.showModal();
    requestAnimationFrame(() => nameField.focus());
  }

  function openCreateForm(event) {
    resetForm();
    showForm(event?.currentTarget);
  }

  function selectType(type, typeButton, value = null) {
    editor?.destroy();
    selectedType = type;
    [...typesHost.children].forEach((item) => item.setAttribute("aria-pressed", String(item === typeButton)));
    editor = mountJobTypeEditor(type.editor_key, root.querySelector("#job-type-editor"), {
      preview: (typeConfig) => api.preview(type.key, typeConfig),
      formatters: formatterProfiles,
    });
    if (value) editor.hydrate(value);
  }

  async function openEditForm(job, trigger, cardStatus) {
    trigger.disabled = true;
    cardStatus.textContent = "Job 설정을 불러오는 중입니다.";
    try {
      const detail = await api.get(job.id);
      resetForm();
      editingJob = detail;
      formTitle.textContent = "Job 설정 편집";
      formEyebrow.textContent = "EDIT AUTOMATION";
      formDescription.textContent = "설정을 바꾸면 Job이 일시정지되고 다시 테스트해야 합니다.";
      formHint.textContent = "변경한 설정은 안전을 위해 자동으로 일시정지됩니다.";
      submitButton.textContent = "변경 저장";
      nameField.value = detail.name;
      kind.value = detail.schedule_kind;
      expressionInput.value = detail.schedule_expression || "";
      timezoneInput.value = detail.timezone || "Asia/Seoul";
      renderSchedule();
      const type = jobTypes.find((item) => item.key === detail.job_type);
      const typeButton = [...typesHost.children].find((item) => item.dataset.typeKey === detail.job_type);
      if (!type || !typeButton) throw new Error("이 Job Type 편집기를 찾을 수 없습니다.");
      selectType(type, typeButton, detail.type_config);
      [...typesHost.children].forEach((item) => { item.disabled = true; });
      typeLockNote.hidden = false;
      cardStatus.textContent = "";
      showForm(trigger);
    } catch (error) {
      cardStatus.textContent = error.message;
    } finally {
      trigger.disabled = false;
    }
  }

  function renderSchedule() {
    expressionField.hidden = kind.value === "MANUAL";
    timezoneField.hidden = kind.value !== "CRON";
  }

  function resetFormatterForm() {
    editingFormatter = null;
    formatterDeleteArmed = false;
    formatterForm.reset();
    formatterModel.value = "gpt-5.6-luna";
    root.querySelector("#jobs-formatter-eyebrow").textContent = "NEW FORMATTER";
    root.querySelector("#jobs-formatter-form-title").textContent = "새 정리 방식";
    formatterDelete.hidden = true;
    formatterDelete.disabled = false;
    formatterDelete.textContent = "삭제";
    formatterStatus.textContent = "수집 원문은 데이터로만 전달되며, 이 지침을 실행 규칙으로 사용합니다.";
    formatterList.querySelectorAll("button[data-profile-id]").forEach((item) => item.setAttribute("aria-pressed", "false"));
  }

  function editFormatter(profile, trigger) {
    editingFormatter = profile;
    formatterDeleteArmed = false;
    formatterName.value = profile.name;
    formatterModel.value = profile.model;
    formatterInstructions.value = profile.instructions;
    root.querySelector("#jobs-formatter-eyebrow").textContent = "EDIT FORMATTER";
    root.querySelector("#jobs-formatter-form-title").textContent = "정리 방식 편집";
    formatterDelete.hidden = profile.is_builtin && !profile.is_overridden;
    formatterDelete.disabled = !profile.is_builtin && profile.use_count > 0;
    formatterDelete.textContent = profile.is_builtin ? "기본값 복원" : "삭제";
    formatterDelete.title = !profile.is_builtin && profile.use_count > 0 ? "사용 중인 정리 방식은 삭제할 수 없습니다." : "";
    if (profile.is_builtin) {
      formatterStatus.textContent = profile.use_count > 0
        ? `기본 제공 방식이며 현재 ${profile.use_count}개 Job에서 사용 중입니다. 변경하거나 복원하면 해당 Job을 다시 테스트해야 합니다.`
        : "기본 제공 금융시장 브리핑입니다. 프롬프트와 모델을 변경할 수 있습니다.";
    } else {
      formatterStatus.textContent = profile.use_count > 0
        ? `현재 ${profile.use_count}개 Job에서 사용 중입니다. 변경하면 해당 Job이 일시정지되고 재테스트가 필요합니다.`
        : "이 정리 방식을 사용하는 Job이 없습니다.";
    }
    formatterList.querySelectorAll("button[data-profile-id]").forEach((item) => item.setAttribute("aria-pressed", String(item === trigger)));
    formatterName.focus();
  }

  function renderFormatterList() {
    if (!formatterProfiles.length) {
      const empty = document.createElement("p");
      empty.className = "jobs-formatter-empty";
      empty.textContent = "아직 만든 사용자 정리 방식이 없습니다.";
      formatterList.replaceChildren(empty);
      return;
    }
    formatterList.replaceChildren(...formatterProfiles.map((profile) => {
      const item = document.createElement("button");
      item.type = "button";
      item.dataset.profileId = profile.id;
      item.setAttribute("aria-pressed", String(editingFormatter?.id === profile.id));
      const name = document.createElement("strong");
      name.textContent = profile.name;
      const meta = document.createElement("small");
      const origin = profile.is_builtin
        ? profile.is_overridden ? "기본 제공 · 수정됨" : "기본 제공"
        : profile.model;
      meta.textContent = `${origin} · ${profile.use_count || 0}개 Job`;
      item.append(name, meta);
      item.addEventListener("click", () => editFormatter(profile, item));
      return item;
    }));
  }

  async function loadFormatters() {
    formatterProfiles = await api.formatters();
    renderFormatterList();
    editor?.setFormatters?.(formatterProfiles);
    return formatterProfiles;
  }

  async function openFormatterDialog(event) {
    formatterReturnFocus = event?.currentTarget || document.activeElement;
    resetFormatterForm();
    formatterStatus.textContent = "정리 방식을 불러오는 중입니다.";
    if (!formatterDialog.open) formatterDialog.showModal();
    try {
      await loadFormatters();
      resetFormatterForm();
      root.querySelector("#jobs-formatter-title").focus();
    } catch (error) {
      formatterStatus.textContent = error.message;
    }
  }

  function button(label, action, className = "") {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    element.dataset.action = action;
    if (className) element.className = className;
    return element;
  }

  function openArchive(job, trigger) {
    confirmationAction = "archive";
    archiveTarget = job;
    archiveReturnFocus = trigger;
    root.querySelector("#job-archive-name").textContent = job.name;
    root.querySelector("#job-confirm-eyebrow").textContent = "ARCHIVE JOB";
    root.querySelector("#job-confirm-title").textContent = "Job을 보관할까요?";
    root.querySelector("#job-confirm-copy").textContent = "은 더 이상 실행되지 않고, 대기 중인 실행은 취소됩니다. 실행 이력은 유지됩니다.";
    root.querySelector("#job-archive-submit").textContent = "보관하기";
    root.querySelector("#job-archive-status").textContent = "";
    if (!archiveDialog.open) archiveDialog.showModal();
    requestAnimationFrame(() => root.querySelector("#job-archive-cancel").focus());
  }

  function openDelete(job, trigger) {
    confirmationAction = "remove";
    archiveTarget = job;
    archiveReturnFocus = trigger;
    root.querySelector("#job-archive-name").textContent = job.name;
    root.querySelector("#job-confirm-eyebrow").textContent = "DELETE JOB";
    root.querySelector("#job-confirm-title").textContent = "Job을 영구 삭제할까요?";
    root.querySelector("#job-confirm-copy").textContent = "과 모든 실행 이력, 수집 결과가 영구 삭제됩니다. 이 작업은 되돌릴 수 없습니다.";
    root.querySelector("#job-archive-submit").textContent = "영구 삭제";
    root.querySelector("#job-archive-status").textContent = "";
    if (!archiveDialog.open) archiveDialog.showModal();
    requestAnimationFrame(() => root.querySelector("#job-archive-cancel").focus());
  }

  function openRunDelete(job, run, trigger) {
    confirmationAction = "removeRun";
    archiveTarget = job;
    runDeleteTarget = run;
    archiveReturnFocus = trigger;
    root.querySelector("#job-archive-name").textContent = formatTime(run.finished_at || run.created_at);
    root.querySelector("#job-confirm-eyebrow").textContent = "DELETE RUN HISTORY";
    root.querySelector("#job-confirm-title").textContent = "실행 기록을 삭제할까요?";
    root.querySelector("#job-confirm-copy").textContent = " 실행과 연결된 재시도 기록 및 수집 결과가 함께 삭제됩니다. 이미 생성된 Note는 유지되며, 이 작업은 되돌릴 수 없습니다.";
    root.querySelector("#job-archive-submit").textContent = "기록 삭제";
    root.querySelector("#job-archive-status").textContent = "";
    if (!archiveDialog.open) archiveDialog.showModal();
    requestAnimationFrame(() => root.querySelector("#job-archive-cancel").focus());
  }

  async function openHistory(job, trigger, cardStatus) {
    historyTargetJob = job;
    historyReturnFocus = trigger;
    historyTitle.textContent = `${job.name} 실행 이력`;
    historyStatus.textContent = "실행 이력을 불러오는 중입니다.";
    historyList.replaceChildren();
    if (!historyDialog.open) historyDialog.showModal();
    try {
      renderRuns(historyList, await api.runs(job.id), job, historyStatus);
      historyStatus.textContent = "";
      cardStatus.textContent = "";
      historyTitle.focus();
    } catch (error) {
      historyStatus.textContent = error.message;
    }
  }

  function formatTime(value) {
    if (!value) return "-";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat("ko-KR", {
      dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul",
    }).format(parsed);
  }

  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const isTestRun = (run) => String(run.dedupe_key || "").startsWith("test:");

  async function followRun(jobId, runId, cardStatus, { test = false } = {}) {
    const terminal = new Set(["SUCCEEDED", "UNCHANGED", "PARTIAL", "FAILED", "CANCELLED"]);
    for (let attempt = 0; attempt < 160; attempt += 1) {
      const runs = await api.runs(jobId);
      const run = runs.find((item) => item.id === runId);
      if (run) {
        cardStatus.textContent = `${test ? "테스트" : "실행"} 상태: ${runLabels[run.status] || run.status}`;
        if (terminal.has(run.status)) {
          if (test) {
            cardStatus.textContent = run.status === "SUCCEEDED"
              ? "현재 설정 테스트에 성공했습니다."
              : `테스트 실패: ${operationMessages[run.outcome_code || run.error_code] || run.outcome_code || run.error_code || run.status}`;
          } else {
            cardStatus.textContent += run.type_result?.note_id != null ? " · Note가 생성되었습니다." : "";
          }
          return run;
        }
      }
      await delay(1500);
    }
    cardStatus.textContent = "Worker 처리를 기다리고 있습니다. 실행 이력에서 진행 상태를 확인하세요.";
    return null;
  }

  function renderRuns(host, runs, job, cardStatus) {
    if (!runs.length) {
      const empty = document.createElement("p");
      empty.textContent = "실행 이력이 없습니다.";
      host.replaceChildren(empty);
      return;
    }
    const items = runs.map((run) => {
      const testRun = isTestRun(run);
      const item = document.createElement("li");
      const summary = document.createElement("div");
      summary.className = "job-run-summary";
      const state = document.createElement("strong");
      state.textContent = `${testRun ? "설정 테스트 · " : ""}${runLabels[run.status] || run.status}`;
      const time = document.createElement("time");
      time.dateTime = run.finished_at || run.created_at;
      time.textContent = formatTime(run.finished_at || run.created_at);
      const summaryActions = document.createElement("div");
      summaryActions.className = "job-run-summary-actions";
      summaryActions.append(time);
      if (!["QUEUED", "RUNNING"].includes(run.status)) {
        const remove = button("−", "remove-run");
        remove.className = "job-run-delete";
        remove.title = "실행 기록 삭제";
        remove.setAttribute("aria-label", `${time.textContent} 실행 기록 삭제`);
        remove.addEventListener("click", () => openRunDelete(job, run, remove));
        summaryActions.append(remove);
      }
      summary.append(state, summaryActions);
      const detail = document.createElement("p");
      const detailCode = run.outcome_code || run.error_code || run.trigger_kind;
      detail.textContent = operationMessages[detailCode] || detailCode;
      item.append(summary, detail);
      const noteId = run.type_result?.note_id;
      if (noteId != null) {
        const link = document.createElement("a");
        link.href = `/w/${encodeURIComponent(root.dataset.workspaceId)}/notes?note_id=${encodeURIComponent(noteId)}`;
        link.textContent = "생성된 Note 열기";
        item.append(link);
      }
      if (!testRun && ["FAILED", "PARTIAL"].includes(run.status) && job.status === "ACTIVE") {
        const retry = button("재시도", "retry-run");
        retry.className = "job-run-retry";
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          cardStatus.textContent = "재시도를 등록하는 중입니다.";
          try {
            const queued = await api.retry(job.id, run.id);
            cardStatus.textContent = "재시도가 대기열에 등록되었습니다.";
            await followRun(job.id, queued.id, cardStatus);
            renderRuns(host, await api.runs(job.id), job, cardStatus);
          } catch (error) {
            cardStatus.textContent = error.message;
            retry.disabled = false;
          }
        });
        item.append(retry);
      }
      return item;
    });
    const history = document.createElement("ol");
    history.className = "job-run-list";
    history.append(...items);
    host.replaceChildren(history);
  }

  async function mutate(job, action, trigger, cardStatus) {
    const labels = { test: "테스트 중", activate: "활성화 중", pause: "일시정지 중", run: "실행 등록 중" };
    const buttons = trigger.parentElement.querySelectorAll("button");
    const disabledStates = new Map([...buttons].map((item) => [item, item.disabled]));
    const originalLabel = trigger.textContent;
    buttons.forEach((item) => { item.disabled = true; });
    trigger.setAttribute("aria-busy", "true");
    if (action === "run") trigger.textContent = "실행 중…";
    if (action === "test") trigger.textContent = "등록 중…";
    cardStatus.textContent = labels[action] || "처리 중";
    try {
      const result = await api[action](job.id);
      if (action === "test") {
        cardStatus.textContent = "설정 테스트를 백그라운드에 등록했습니다.";
        void followRun(job.id, result.id, cardStatus, { test: true })
          .then((run) => run?.status === "SUCCEEDED" ? loadJobs() : undefined)
          .catch((error) => { cardStatus.textContent = error.message; });
        return;
      } else if (action === "run") {
        cardStatus.textContent = "실행 대기열에 등록했습니다.";
        await followRun(job.id, result.id, cardStatus);
        return;
      } else {
        cardStatus.textContent = action === "activate" ? "Job을 활성화했습니다." : "Job을 일시정지했습니다.";
      }
      await loadJobs();
    } catch (error) {
      cardStatus.textContent = error.message;
    } finally {
      trigger.textContent = originalLabel;
      trigger.removeAttribute("aria-busy");
      buttons.forEach((item) => {
        if (item.isConnected) item.disabled = disabledStates.get(item) || false;
      });
    }
  }

  function renderJob(job) {
    const card = document.createElement("article");
    card.className = "job-card";
    const heading = document.createElement("div");
    heading.className = "job-card-heading";
    const title = document.createElement("h4");
    title.textContent = job.name;
    const badge = document.createElement("span");
    badge.className = `job-status job-status-${job.status.toLowerCase()}`;
    badge.textContent = statusLabels[job.status] || job.status;
    heading.append(title, badge);
    const detail = document.createElement("p");
    detail.textContent = `${job.job_type} · revision ${job.definition_revision}`;
    const cardStatus = document.createElement("p");
    cardStatus.className = "job-card-status";
    cardStatus.setAttribute("role", "status");
    cardStatus.setAttribute("aria-live", "polite");
    const actions = document.createElement("div");
    actions.className = "job-card-actions";
    if (job.status !== "ARCHIVED") actions.append(button("테스트", "test"));
    if (job.status === "ACTIVE") {
      actions.append(button("지금 실행", "run", "jobs-primary"), button("일시정지", "pause"));
    } else if (job.status !== "ARCHIVED") {
      const activate = button("활성화", "activate", "jobs-primary");
      if (job.last_tested_revision !== job.definition_revision) {
        activate.disabled = true;
        activate.title = "현재 설정을 먼저 테스트하세요.";
      }
      actions.append(activate);
    }
    const historyButton = button("실행 이력", "history");
    historyButton.setAttribute("aria-haspopup", "dialog");
    actions.append(historyButton);
    if (job.status !== "ARCHIVED") {
      actions.append(button("편집", "edit"), button("보관", "archive", "jobs-danger-link"));
    } else {
      actions.append(button("영구 삭제", "remove", "jobs-danger-link"));
    }
    actions.addEventListener("click", async (event) => {
      const trigger = event.target.closest("button[data-action]");
      if (!trigger) return;
      if (trigger.dataset.action === "edit") {
        await openEditForm(job, trigger, cardStatus);
        return;
      }
      if (trigger.dataset.action === "archive") {
        openArchive(job, trigger);
        return;
      }
      if (trigger.dataset.action === "remove") {
        openDelete(job, trigger);
        return;
      }
      if (trigger.dataset.action === "history") {
        trigger.disabled = true;
        await openHistory(job, trigger, cardStatus);
        trigger.disabled = false;
        return;
      }
      await mutate(job, trigger.dataset.action, trigger, cardStatus);
    });
    card.append(heading, detail, actions, cardStatus);
    return card;
  }

  async function loadTypes() {
    jobTypes = await api.types();
    typesHost.replaceChildren(...jobTypes.map((type) => {
      const typeButton = button(`${type.label} — ${type.description}`, "select-type");
      typeButton.className = "job-type-card";
      typeButton.dataset.typeKey = type.key;
      typeButton.setAttribute("aria-pressed", "false");
      typeButton.addEventListener("click", () => {
        if (!editingJob) selectType(type, typeButton);
      });
      return typeButton;
    }));
  }

  async function loadJobs() {
    const jobs = await api.list();
    root.querySelector("#jobs-count-all").textContent = String(jobs.length);
    root.querySelector("#jobs-count-active").textContent = String(jobs.filter((job) => job.status === "ACTIVE").length);
    root.querySelector("#jobs-count-attention").textContent = String(jobs.filter((job) => job.status === "PAUSED" || job.consecutive_failures > 0).length);
    if (jobs.length) {
      list.replaceChildren(...jobs.map(renderJob));
    } else {
      const empty = document.createElement("section");
      empty.className = "jobs-empty";
      const mark = document.createElement("div");
      mark.className = "jobs-empty-mark";
      mark.setAttribute("aria-hidden", "true");
      const heading = document.createElement("h4");
      heading.textContent = "아직 등록된 Job이 없습니다";
      const copy = document.createElement("p");
      copy.textContent = "웹페이지의 변화를 자동으로 기록할 첫 Job을 만들어보세요.";
      const create = button("첫 Job 만들기", "empty-create", "jobs-primary");
      create.addEventListener("click", openCreateForm);
      empty.append(mark, heading, copy, create);
      list.replaceChildren(empty);
    }
    status.textContent = jobs.length ? `${jobs.length}개의 Job을 관리하고 있습니다.` : "새 Job을 만들어 자동화를 시작하세요.";
  }

  async function loadHealth() {
    const worker = root.querySelector("#jobs-worker-state");
    try {
      const health = await api.health();
      const state = health.worker?.availability || "unknown";
      const labels = { healthy: "정상", stale: "응답 없음", failed: "오류", stopped: "중지됨", unknown: "미실행" };
      worker.textContent = labels[state] || "확인 필요";
      worker.dataset.state = state;
      if (health.worker?.last_heartbeat_at) {
        const version = health.worker.version ? ` · Worker v${health.worker.version}` : "";
        worker.title = `마지막 신호 ${formatTime(health.worker.last_heartbeat_at)}${version}`;
      }
    } catch (_error) {
      worker.textContent = "확인 실패";
      worker.dataset.state = "failed";
    }
  }

  kind.addEventListener("change", renderSchedule);
  root.querySelector("#jobs-new").addEventListener("click", openCreateForm);
  root.querySelector("#jobs-formatters").addEventListener("click", openFormatterDialog);
  root.querySelector("#job-cancel").addEventListener("click", closeForm);
  root.querySelector('[data-action="cancel-form"]').addEventListener("click", closeForm);
  createDialog.addEventListener("close", () => {
    const target = formReturnFocus?.isConnected ? formReturnFocus : root.querySelector("#jobs-new");
    resetForm();
    target.focus();
  });

  root.querySelector("#job-archive-cancel").addEventListener("click", () => archiveDialog.close());
  root.querySelector("#jobs-history-close").addEventListener("click", () => historyDialog.close());
  root.querySelector("#jobs-formatter-close").addEventListener("click", () => formatterDialog.close());
  root.querySelector("#jobs-formatter-new").addEventListener("click", () => {
    resetFormatterForm();
    formatterName.focus();
  });
  historyDialog.addEventListener("close", () => {
    const target = historyReturnFocus?.isConnected ? historyReturnFocus : root.querySelector("#jobs-new");
    historyTargetJob = null;
    historyList.replaceChildren();
    historyStatus.textContent = "";
    target.focus();
  });
  formatterDialog.addEventListener("close", () => {
    const target = formatterReturnFocus?.isConnected ? formatterReturnFocus : root.querySelector("#jobs-formatters");
    resetFormatterForm();
    target.focus();
  });
  formatterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!formatterForm.reportValidity()) return;
    const submit = formatterForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    const wasEditing = Boolean(editingFormatter);
    formatterStatus.textContent = wasEditing ? "정리 방식을 변경하는 중입니다." : "정리 방식을 만드는 중입니다.";
    try {
      const payload = {
        name: formatterName.value,
        model: formatterModel.value,
        instructions: formatterInstructions.value,
      };
      if (editingFormatter) {
        await api.updateFormatter(editingFormatter.id, { ...payload, expected_revision: editingFormatter.revision });
      } else {
        await api.createFormatter(payload);
      }
      await loadFormatters();
      if (wasEditing) await loadJobs();
      resetFormatterForm();
      formatterStatus.textContent = wasEditing ? "정리 방식을 변경했습니다." : "새 정리 방식을 만들었습니다.";
    } catch (error) {
      formatterStatus.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  formatterDelete.addEventListener("click", async () => {
    if (!editingFormatter || (!editingFormatter.is_builtin && editingFormatter.use_count > 0)) return;
    if (!formatterDeleteArmed) {
      formatterDeleteArmed = true;
      formatterDelete.textContent = editingFormatter.is_builtin ? "한 번 더 눌러 복원" : "한 번 더 눌러 삭제";
      formatterStatus.textContent = editingFormatter.is_builtin
        ? "저장소의 기본 금융시장 프롬프트와 모델로 돌아갑니다. 복원 버튼을 한 번 더 누르세요."
        : "삭제하면 복구할 수 없습니다. 삭제 버튼을 한 번 더 누르세요.";
      return;
    }
    formatterDelete.disabled = true;
    const wasBuiltin = Boolean(editingFormatter.is_builtin);
    formatterStatus.textContent = wasBuiltin
      ? "기본 정리 방식으로 복원하는 중입니다."
      : "정리 방식을 삭제하는 중입니다.";
    try {
      await api.removeFormatter(editingFormatter.id);
      await loadFormatters();
      resetFormatterForm();
      formatterStatus.textContent = wasBuiltin
        ? "기본 금융시장 정리 방식으로 복원했습니다."
        : "정리 방식을 삭제했습니다.";
    } catch (error) {
      formatterDeleteArmed = false;
      formatterDelete.textContent = editingFormatter?.is_builtin ? "기본값 복원" : "삭제";
      formatterDelete.disabled = false;
      formatterStatus.textContent = error.message;
    }
  });
  archiveDialog.addEventListener("close", () => {
    const target = archiveReturnFocus?.isConnected ? archiveReturnFocus : root.querySelector("#jobs-new");
    archiveTarget = null;
    runDeleteTarget = null;
    target.focus();
  });
  root.querySelector("#job-archive-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!archiveTarget) return;
    const submit = root.querySelector("#job-archive-submit");
    const archiveStatus = root.querySelector("#job-archive-status");
    submit.disabled = true;
    const removing = confirmationAction === "remove";
    const removingRun = confirmationAction === "removeRun";
    archiveStatus.textContent = removingRun
      ? "실행 기록을 삭제하는 중입니다."
      : removing ? "Job과 실행 이력을 삭제하는 중입니다." : "Job을 보관하는 중입니다.";
    try {
      const targetJob = archiveTarget;
      const targetRun = runDeleteTarget;
      if (removingRun) {
        await api.removeRun(targetJob.id, targetRun.id);
      } else {
        await api[confirmationAction](targetJob.id);
      }
      archiveDialog.close();
      if (removingRun && historyDialog.open && historyTargetJob?.id === targetJob.id) {
        renderRuns(historyList, await api.runs(targetJob.id), historyTargetJob, historyStatus);
        historyStatus.textContent = "실행 기록을 삭제했습니다.";
        historyTitle.focus();
      } else {
        await loadJobs();
        status.textContent = removing
          ? "Job과 실행 이력을 영구 삭제했습니다."
          : "Job을 보관했습니다. 실행 이력은 그대로 유지됩니다.";
      }
    } catch (error) {
      archiveStatus.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedType) {
      formStatus.textContent = "수집 방식을 선택하세요.";
      typesHost.querySelector("button")?.focus();
      return;
    }
    if (!editor?.validate()) {
      formStatus.textContent = "입력한 수집 설정을 확인하세요.";
      form.querySelector(":invalid")?.focus();
      return;
    }
    const submit = submitButton;
    submit.disabled = true;
    formStatus.textContent = editingJob ? "변경 사항을 저장하는 중입니다." : "Job을 저장하는 중입니다.";
    try {
      const expression = expressionInput.value.trim() || null;
      const payload = {
        name: nameField.value,
        schedule: { kind: kind.value, expression: kind.value === "MANUAL" ? null : expression, timezone: kind.value === "CRON" ? root.querySelector("#job-timezone").value : null },
        type_config: editor.serialize(),
      };
      if (editingJob) {
        await api.update(editingJob.id, { ...payload, expected_revision: editingJob.definition_revision });
      } else {
        await api.create({ ...payload, job_type: selectedType.key });
      }
      const wasEditing = Boolean(editingJob);
      closeForm();
      await loadJobs();
      status.textContent = wasEditing ? "Job 설정을 변경했습니다. 다시 테스트한 뒤 활성화하세요." : "새 Job 초안을 저장했습니다. 테스트한 뒤 활성화하세요.";
    } catch (error) {
      formStatus.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  Promise.all([loadFormatters(), loadTypes(), loadJobs(), loadHealth()])
    .catch((error) => { status.textContent = error.message; })
    .finally(() => globalThis.workspaceAppReady?.());
}
