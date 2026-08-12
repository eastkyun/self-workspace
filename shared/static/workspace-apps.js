// Read-compatible pin migration. The v1 value is deliberately preserved.
export function migrateLegacyPins(storage, eligible, userId, workspaceId) {
  const legacyKey = "my-workspace:pinned-apps:v1";
  const scopedKey = `my-workspace:pinned-apps:v2:${userId}:${workspaceId}`;
  try {
    if (!eligible || storage.getItem(scopedKey) !== null) return;
    const legacy = JSON.parse(storage.getItem(legacyKey) || "[]");
    if (Array.isArray(legacy)) storage.setItem(scopedKey, JSON.stringify(legacy));
  } catch { /* malformed legacy preferences are ignored */ }
}

function csrfToken(cookieName) {
  return decodeURIComponent(
    (document.cookie.split("; ").find((item) => item.startsWith(`${cookieName}_csrf=`)) || "=")
      .split("=")
      .slice(1)
      .join("="),
  );
}

function setStatus(status, message, kind) {
  if (!status) return;
  status.textContent = message;
  status.classList.remove("is-success", "is-error", "is-pending");
  if (kind) status.classList.add(`is-${kind}`);
}

function initializeWorkspaceApps() {
  const root = document.querySelector("[data-workspace-app-settings]");
  if (!root) return;

  const workspaceId = root.dataset.workspaceId;
  const userId = root.dataset.userId;
  const cookieName = document.body.dataset.sessionCookie || "my_workspace_session";
  const list = root.querySelector("#workspace-app-list");
  const status = root.querySelector("#app-settings-status");

  migrateLegacyPins(
    localStorage,
    root.dataset.pinImportEligible === "true",
    userId,
    workspaceId,
  );

  list?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-app-toggle]");
    if (!button || button.disabled || button.dataset.saving === "true") return;
    const enabled = button.dataset.enabled !== "true";
    const originalLabel = button.textContent;
    button.dataset.saving = "true";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "변경 중…";
    setStatus(status, "앱 설정을 변경하고 있습니다…", "pending");

    try {
      const response = await fetch(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/apps/${encodeURIComponent(button.dataset.appToggle)}`,
        {
          method: "PATCH",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken(cookieName)},
          body: JSON.stringify({enabled}),
        },
      );
      if (!response.ok) throw new Error("앱 설정을 변경하지 못했습니다.");
      setStatus(status, "앱 설정을 변경했습니다.", "success");
      window.location.reload();
    } catch (_) {
      setStatus(status, "앱 설정을 변경하지 못했습니다.", "error");
      delete button.dataset.saving;
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = originalLabel;
    }
  });
}

if (typeof document !== "undefined") initializeWorkspaceApps();
