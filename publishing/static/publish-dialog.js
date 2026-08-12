function apiErrorMessage(payload) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (typeof payload?.detail?.message === "string") return payload.detail.message;
  return "발행 요청을 처리하지 못했습니다.";
}

export function createPublishingApi({
  publicationsUrl,
  typesUrl,
  fetchImpl = fetch,
  csrfToken = () => "",
}) {
  async function request(url, options = {}) {
    const response = await fetchImpl(url, {
      ...options,
      headers: { Accept: "application/json", ...options.headers },
    });
    if (response.ok) return response.json();
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      // Stable local fallback below.
    }
    const error = new Error(apiErrorMessage(payload));
    error.status = response.status;
    throw error;
  }

  function json(method, body) {
    return {
      method,
      headers: mutationHeaders(csrfToken()),
      body: JSON.stringify(body),
    };
  }

  return Object.freeze({
    types() {
      return request(typesUrl);
    },
    listForNote(noteId) {
      const separator = publicationsUrl.includes("?") ? "&" : "?";
      return request(
        `${publicationsUrl}${separator}note_id=${encodeURIComponent(noteId)}`,
      );
    },
    preview(payload) {
      return request(`${publicationsUrl}/preview-from-note`, json("POST", payload));
    },
    create(payload) {
      return request(publicationsUrl, json("POST", payload));
    },
    saveDraft(publicationId, payload) {
      return request(
        `${publicationsUrl}/${publicationId}/drafts/from-note`,
        json("POST", payload),
      );
    },
    publish(publicationId) {
      return request(`${publicationsUrl}/${publicationId}/publish`, {
        method: "POST",
        headers: mutationHeaders(csrfToken()),
      });
    },
  });
}

function mutationHeaders(token) {
  return {
    "Content-Type": "application/json",
    ...(token ? { "X-CSRF-Token": token } : {}),
  };
}

export function suggestPublicationSlug(title) {
  return String(title ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 80)
    .replace(/-+$/g, "");
}

export function publicationTargetValue(publicationId) {
  return publicationId ? `publication:${publicationId}` : "new";
}

export function publicationIdFromTarget(value) {
  const match = /^publication:([1-9]\d*)$/.exec(String(value ?? ""));
  return match ? Number(match[1]) : null;
}
