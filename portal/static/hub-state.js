export function defaultPinnedIds(apps) {
  return apps.filter((app) => app.default_pinned).map((app) => app.id);
}

export function decodePinnedIds(serialized, apps) {
  const validIds = new Set(apps.map((app) => app.id));
  if (serialized === null) return defaultPinnedIds(apps);

  try {
    const parsed = JSON.parse(serialized);
    if (!Array.isArray(parsed)) return defaultPinnedIds(apps);
    return [...new Set(parsed.filter((id) => validIds.has(id)))];
  } catch {
    return defaultPinnedIds(apps);
  }
}

export function workspaceAppRoute(app, workspaceId) {
  if (workspaceId && ["calendar", "notes", "publishing", "jobs"].includes(app.id)) {
    const slug = app.id === "publishing" ? "publications" : app.id;
    return `/w/${workspaceId}/${slug}`;
  }
  return app.route;
}
