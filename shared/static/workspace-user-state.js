export function createWorkspaceUserState({ baseUrl, csrfToken = () => "" }) {
  async function request(path, options = {}) {
    const token = csrfToken();
    const response = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-CSRF-Token": token } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) throw new Error("Workspace state request failed");
    return response.status === 204 ? null : response.json();
  }
  const resourcePath = (collection, type, id) =>
    `/${collection}/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
  return {
    favorites: () => request("/favorites"),
    favorite: (type, id, payload) => request(resourcePath("favorites", type, id), {method: "PUT", body: JSON.stringify({app_key: payload.app_key})}),
    unfavorite: (type, id) => request(resourcePath("favorites", type, id), {method: "DELETE"}),
    recent: (type, id, payload) => request("/recent-views", {method: "POST", body: JSON.stringify({resource_type: type, resource_id: String(id), app_key: payload.app_key})}),
  };
}
