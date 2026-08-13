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
    list: () => request(),
    create: (payload) => request("", { method: "POST", body: JSON.stringify(payload) }),
  };
}
