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
  const form = root.querySelector("#jobs-form");
  const typesHost = root.querySelector("#job-types");
  const kind = root.querySelector("#job-schedule-kind");
  const expressionField = root.querySelector("#job-expression-field");
  const timezoneField = root.querySelector("#job-timezone-field");
  let selectedType = null;
  let editor = null;

  function renderSchedule() {
    expressionField.hidden = kind.value === "MANUAL";
    timezoneField.hidden = kind.value !== "CRON";
  }
  kind.addEventListener("change", renderSchedule);
  root.querySelector("#jobs-new").addEventListener("click", () => { form.hidden = false; });
  root.querySelector("#job-cancel").addEventListener("click", () => { form.hidden = true; });

  async function load() {
    const [types, jobs] = await Promise.all([api.types(), api.list()]);
    typesHost.replaceChildren(...types.map((type) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "job-type-card";
      button.textContent = `${type.label} — ${type.description}`;
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        editor?.destroy(); selectedType = type;
        [...typesHost.children].forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        editor = mountJobTypeEditor(type.editor_key, root.querySelector("#job-type-editor"));
      });
      return button;
    }));
    list.replaceChildren(...jobs.map((job) => {
      const card = document.createElement("article"); card.className = "job-card";
      const title = document.createElement("h4"); title.textContent = job.name;
      const detail = document.createElement("p"); detail.textContent = `${job.job_type} · ${job.status}`;
      card.append(title, detail); return card;
    }));
    status.textContent = jobs.length ? `${jobs.length}개의 Job` : "등록된 Job이 없습니다.";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedType || !editor?.validate()) return;
    const expression = root.querySelector("#job-schedule-expression").value.trim() || null;
    await api.create({
      name: root.querySelector("#job-name").value,
      job_type: selectedType.key,
      schedule: { kind: kind.value, expression: kind.value === "MANUAL" ? null : expression, timezone: kind.value === "CRON" ? root.querySelector("#job-timezone").value : null },
      type_config: editor.serialize(),
    });
    form.reset(); editor.destroy(); editor = null; selectedType = null; form.hidden = true; renderSchedule(); await load();
  });

  load().catch((error) => { status.textContent = error.message; }).finally(() => globalThis.workspaceAppReady?.());
}
