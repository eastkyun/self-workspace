export function createNotesApi(baseUrl, fetchImpl = fetch) {
  async function request(path = "", options = {}) {
    const response = await fetchImpl(`${baseUrl}${path}`, {
      ...options,
      headers: { Accept: "application/json", ...options.headers },
    });
    if (response.ok) {
      if (response.status === 204) {
        await response.arrayBuffer();
        return null;
      }
      return response.json();
    }
    let message = "요청을 처리하지 못했습니다.";
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : message;
    } catch {
      // Keep the stable local error message for non-JSON failures.
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return {
    list({ query = "", noteType = "" } = {}) {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (noteType) params.set("type", noteType);
      const suffix = params.size ? `?${params}` : "";
      return request(suffix);
    },
    get(noteId) {
      return request(`/${noteId}`);
    },
    create(payload) {
      return request("", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    update(noteId, payload) {
      return request(`/${noteId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    remove(noteId) {
      return request(`/${noteId}`, { method: "DELETE" });
    },
    links(noteId) {
      return request(`/${noteId}/links`);
    },
    unlink(noteId, linkId) {
      return request(`/${noteId}/links/${linkId}`, { method: "DELETE" });
    },
  };
}
