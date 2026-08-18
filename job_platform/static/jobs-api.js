export function createJobsApi(baseUrl, fetchImpl = fetch, csrfToken = () => "") {
  async function request(path = "", options = {}) {
    const headers = { Accept: "application/json", "Content-Type": "application/json", ...(options.headers || {}) };
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrfToken();
    const response = await fetchImpl(`${baseUrl}${path}`, { ...options, headers });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Jobs 요청에 실패했습니다.");
    return body;
  }
  return {
    types: () => request("/types"),
    formatters: () => request("/formatters"),
    getFormatter: (profileId) => request(`/formatters/${encodeURIComponent(profileId)}`),
    createFormatter: (payload) => request("/formatters", { method: "POST", body: JSON.stringify(payload) }),
    updateFormatter: (profileId, payload) => request(`/formatters/${encodeURIComponent(profileId)}`, { method: "PUT", body: JSON.stringify(payload) }),
    removeFormatter: (profileId) => request(`/formatters/${encodeURIComponent(profileId)}`, { method: "DELETE" }),
    list: () => request(),
    get: (jobId) => request(`/${encodeURIComponent(jobId)}`),
    health: () => request("/health"),
    preview: (jobType, typeConfig) => request(`/types/${encodeURIComponent(jobType)}/preview`, { method: "POST", body: JSON.stringify({ type_config: typeConfig }) }),
    create: (payload) => request("", { method: "POST", body: JSON.stringify(payload) }),
    update: (jobId, payload) => request(`/${encodeURIComponent(jobId)}`, { method: "PUT", body: JSON.stringify(payload) }),
    archive: (jobId) => request(`/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
    remove: (jobId) => request(`/${encodeURIComponent(jobId)}/permanent`, { method: "DELETE" }),
    test: (jobId) => request(`/${encodeURIComponent(jobId)}/test`, { method: "POST" }),
    activate: (jobId) => request(`/${encodeURIComponent(jobId)}/activate`, { method: "POST" }),
    pause: (jobId) => request(`/${encodeURIComponent(jobId)}/pause`, { method: "POST" }),
    run: (jobId) => request(`/${encodeURIComponent(jobId)}/run`, { method: "POST" }),
    runs: (jobId) => request(`/${encodeURIComponent(jobId)}/runs`),
    removeRun: (jobId, runId) => request(`/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
    retry: (jobId, runId) => request(`/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/retry`, { method: "POST" }),
  };
}
