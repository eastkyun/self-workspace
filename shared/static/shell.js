const PIN_STORAGE_KEY = "my-workspace:pinned-apps:v1";
const SIDEBAR_STORAGE_KEY = "my-workspace:sidebar-collapsed:v1";
const desktopQuery = window.matchMedia("(min-width: 761px)");
const body = document.body;
const sidebar = document.querySelector("#sidebar");
const sidebarOpen = document.querySelector("#sidebar-open");
const sidebarClose = document.querySelector("#sidebar-close");
const sidebarCollapse = document.querySelector("#sidebar-collapse");
const sidebarScrim = document.querySelector("#sidebar-scrim");
const workspaceFrame = document.querySelector(".workspace-frame");
const launcher = document.querySelector("#launcher");
const launcherClose = document.querySelector("#launcher-close");
const launcherTriggers = [...document.querySelectorAll(".workspace-launcher-trigger")];
const pinButtons = [...document.querySelectorAll("[data-pin-app]")];
const registeredIds = new Set(
  [...document.querySelectorAll(".workspace-launcher-item[data-app-id]")]
    .map((item) => item.dataset.appId),
);
const appRoot = new URL("../../", import.meta.url);
const guestActions = document.querySelector("#workspace-guest-actions");
const account = document.querySelector("#workspace-account");
const accountToggle = document.querySelector("#workspace-account-toggle");
const accountMenu = document.querySelector("#workspace-account-menu");
const accountLabel = document.querySelector("#workspace-account-label");
const accountAvatar = document.querySelector("#workspace-account-avatar");
const accountName = document.querySelector("#workspace-account-name");
const accountEmail = document.querySelector("#workspace-account-email");
const invitationsDialog = document.querySelector("#workspace-invitations-dialog");
const invitationBadges = [...document.querySelectorAll("[data-invitations-badge]")];
const appLoading = document.querySelector("#workspace-app-loading");
const workspaceMain = document.querySelector("#workspace-main");
const workspaceList = document.querySelector("[data-workspace-list]");
const workspaceStatus = document.querySelector("[data-workspace-status]");
let appLoadingFallback = null;
let workspaceSelectionPending = false;

function finishAppLoading() {
  if (!appLoading || appLoading.hidden || appLoading.classList.contains("is-leaving")) return;
  window.clearTimeout(appLoadingFallback);
  workspaceMain?.setAttribute("aria-busy", "false");
  appLoading.setAttribute("aria-hidden", "true");
  appLoading.classList.add("is-leaving");
  window.setTimeout(() => {
    appLoading.hidden = true;
  }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180);
}

globalThis.workspaceAppReady = finishAppLoading;

async function loadWorkspaceList() {
  if (!workspaceList) return;
  try {
    const response = await fetch(new URL("api/v1/workspaces", appRoot), {headers: {Accept: "application/json"}});
    if (!response.ok) return;
    const workspaces = await response.json();
    workspaceList.replaceChildren(...workspaces.map((item) => {
      const link = document.createElement("a"); link.className = `workspace-workspace-link${item.id === body.dataset.workspaceId ? " is-active" : ""}`; link.href = `/w/${encodeURIComponent(item.id)}/home`; link.title = item.name; link.dataset.workspaceId = item.id;
      const mark = document.createElement("span"); mark.textContent = item.name.trim().charAt(0) || "W"; const name = document.createElement("strong"); name.textContent = item.name; link.append(mark, name); return link;
    }));
  } catch { /* navigation remains usable without the optional list */ }
}
loadWorkspaceList();

workspaceList?.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-workspace-id]");
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (workspaceSelectionPending) return;
  workspaceSelectionPending = true;
  workspaceList.setAttribute("aria-busy", "true");
  if (workspaceStatus) workspaceStatus.textContent = "";
  try {
    const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(link.dataset.workspaceId)}/select`, {
      method: "POST",
      headers: {"X-CSRF-Token": csrfToken()},
    });
    if (!response.ok) throw new Error("workspace selection failed");
    window.location.assign(link.href);
  } catch {
    if (workspaceStatus) workspaceStatus.textContent = "워크스페이스를 전환하지 못했습니다. 다시 시도해 주세요.";
  } finally {
    workspaceSelectionPending = false;
    workspaceList.removeAttribute("aria-busy");
  }
});

async function refreshInvitationBadge() {
  if (!invitationBadges.length) return;
  try {
    const response = await fetch(new URL("api/v1/invitations", appRoot), {headers: {Accept: "application/json"}});
    if (!response.ok) return;
    const invitations = await response.json();
    invitationBadges.forEach((badge) => { badge.hidden = invitations.length === 0; });
  } catch { /* optional notification */ }
}
refreshInvitationBadge();

if (document.querySelector("[data-workspace-await-ready]")) {
  appLoadingFallback = window.setTimeout(finishAppLoading, 15_000);
} else {
  window.requestAnimationFrame(() => window.requestAnimationFrame(finishAppLoading));
}

function canonicalAppRoute(appId, workspaceId) {
  if (!workspaceId || !["calendar", "notes", "publishing"].includes(appId)) return null;
  const slug = appId === "publishing" ? "publications" : appId;
  return new URL(`w/${workspaceId}/${slug}`, appRoot).pathname;
}

function rewriteWorkspaceAppLinks(workspaceId) {
  for (const appId of ["calendar", "notes", "publishing"]) {
    const route = canonicalAppRoute(appId, workspaceId);
    if (!route) continue;
    for (const item of document.querySelectorAll(`[data-app-id="${appId}"]`)) {
      const link = item.matches("a") ? item : item.querySelector("a");
      if (link) link.href = route;
    }
  }
}

function csrfToken() {
  const item = document.cookie.split("; ").find((entry) => entry.split("=", 1)[0].endsWith("_csrf"));
  return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
}

async function loadAccount() {
  try {
    const response = await fetch(new URL("api/v1/users/me", appRoot), { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("guest");
    const user = await response.json();
    const display = user.display_name || user.email;
    accountLabel.textContent = display;
    const avatar = globalThis.workspaceAvatar;
    accountAvatar.innerHTML = avatar ? avatar.avatarSvg(avatar.avatarSeed(user.id), 28) : (display.trim().charAt(0).toUpperCase() || "U");
    accountName.textContent = display;
    accountEmail.textContent = user.email;
    const activeWorkspaceId = body.dataset.workspaceId || user.last_workspace_id;
    rewriteWorkspaceAppLinks(activeWorkspaceId);
    window.dispatchEvent(new CustomEvent("workspace:account-loaded", {
      detail: { workspaceId: activeWorkspaceId },
    }));
    account.hidden = false;
    return user;
  } catch {
    guestActions.hidden = false;
    return null;
  }
}

accountToggle?.addEventListener("click", () => {
  const open = accountMenu.hidden;
  accountMenu.hidden = !open;
  accountToggle.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!account?.hidden && !account?.contains(event.target)) {
    accountMenu.hidden = true;
    accountToggle?.setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && accountMenu && !accountMenu.hidden) {
    accountMenu.hidden = true;
    accountToggle?.setAttribute("aria-expanded", "false");
    accountToggle?.focus();
  }
});
document.querySelector("#workspace-logout")?.addEventListener("click", async () => {
  const response = await fetch(new URL("api/v1/auth/logout", appRoot), {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken() },
  });
  if (response.ok) window.location.assign(new URL("login", appRoot));
});

const renderInvitations = async () => {
  const list = document.querySelector("#workspace-invitations-dialog-list");
  if (!list) return;
  try {
    const response = await fetch(new URL("api/v1/invitations", appRoot), {headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error();
    const invitations = await response.json();
    list.replaceChildren();
    if (!invitations.length) { list.innerHTML = '<div class="workspace-invitations-dialog-empty"><strong>새로운 초대가 없습니다.</strong><span>workspace 초대를 받으면 이곳에 표시됩니다.</span></div>'; return; }
    for (const invitation of invitations) {
      const item = document.createElement("article"); item.className = "workspace-invitations-dialog-item";
      const copy = document.createElement("div"); const title = document.createElement("strong"); title.textContent = invitation.name; const detail = document.createElement("span"); detail.textContent = `${invitation.role_key} 권한 · ${new Date(invitation.expires_at).toLocaleDateString("ko-KR")}까지`; copy.append(title, detail);
      const actions = document.createElement("div"); actions.className = "workspace-invitations-dialog-actions";
      for (const action of ["accept", "reject"]) { const button = document.createElement("button"); button.dataset.action = action; button.dataset.invitationId = invitation.id; button.textContent = action === "accept" ? "참여" : "거절"; actions.append(button); }
      item.append(copy, actions); list.append(item);
    }
  } catch { list.textContent = "초대 목록을 불러오지 못했습니다."; }
};
document.querySelector("#workspace-invitations-open")?.addEventListener("click", () => { accountMenu.hidden = true; accountToggle.setAttribute("aria-expanded", "false"); invitationsDialog?.showModal(); renderInvitations(); });
document.querySelector("#workspace-invitations-close")?.addEventListener("click", () => invitationsDialog?.close());
document.querySelector("#workspace-invitations-dialog-list")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]"); if (!button) return;
  button.disabled = true;
  const response = await fetch(`/api/v1/invitations/received/${encodeURIComponent(button.dataset.invitationId)}/${button.dataset.action}`, {method: "POST", headers: {"X-CSRF-Token": csrfToken()}});
  if (response.ok && button.dataset.action === "accept") { const body = await response.json(); refreshInvitationBadge(); window.location.assign(`/w/${encodeURIComponent(body.workspace_id)}/home`); return; }
  if (response.ok) { button.closest(".workspace-invitations-dialog-item")?.remove(); refreshInvitationBadge(); return; }
  button.disabled = false;
});
globalThis.workspaceAccountPromise = loadAccount();

let launcherTrigger = null;

function storedPinnedIds() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PIN_STORAGE_KEY));
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((id) => typeof id === "string" && registeredIds.has(id));
  } catch {
    return null;
  }
}

function defaultPinnedIds() {
  return pinButtons
    .filter((button) => button.dataset.defaultPinned === "true")
    .map((button) => button.dataset.pinApp);
}

let pinnedIds = new Set(storedPinnedIds() ?? defaultPinnedIds());

function savePinnedIds() {
  try {
    localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify([...pinnedIds]));
  } catch {
    // The shell remains usable when storage is unavailable.
  }
}

function renderPins() {
  for (const button of pinButtons) {
    const appId = button.dataset.pinApp;
    const appName = button.closest("[data-app-id]")?.querySelector("a span")?.textContent ?? "앱";
    const pinned = pinnedIds.has(appId);
    button.textContent = pinned ? "★" : "☆";
    button.setAttribute("aria-pressed", String(pinned));
    button.setAttribute("aria-label", `${appName} ${pinned ? "고정 해제" : "고정"}`);
  }
  window.dispatchEvent(new CustomEvent("workspace:pins-changed", {
    detail: { pinnedIds: [...pinnedIds] },
  }));
}

function notifyLayoutChange() {
  window.dispatchEvent(new CustomEvent("workspace:layout-change"));
}

function setMobileSidebar(open, { restoreFocus = true } = {}) {
  body.classList.toggle("sidebar-open", open);
  sidebarOpen?.setAttribute("aria-expanded", String(open));
  sidebar?.setAttribute("aria-hidden", String(!open));
  if (workspaceFrame) workspaceFrame.inert = open;
  if (open) sidebarClose?.focus();
  else if (restoreFocus) sidebarOpen?.focus();
}

function setDesktopSidebar(collapsed) {
  body.classList.toggle("sidebar-collapsed", collapsed);
  sidebarCollapse?.setAttribute("aria-expanded", String(!collapsed));
  sidebarCollapse?.setAttribute("aria-label", collapsed ? "사이드바 펼치기" : "사이드바 접기");
  if (sidebarCollapse) sidebarCollapse.textContent = collapsed ? "›" : "‹";
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed));
  } catch {
    // The collapse state is optional.
  }
  window.requestAnimationFrame(notifyLayoutChange);
}

function focusableSidebarElements() {
  return [...sidebar.querySelectorAll("a[href], button:not([disabled])")]
    .filter((element) => element.getClientRects().length > 0);
}

sidebarOpen?.addEventListener("click", () => setMobileSidebar(true));
sidebarClose?.addEventListener("click", () => setMobileSidebar(false));
sidebarScrim?.addEventListener("click", () => setMobileSidebar(false));
sidebarCollapse?.addEventListener("click", () => {
  setDesktopSidebar(!body.classList.contains("sidebar-collapsed"));
});

sidebar?.addEventListener("transitionend", notifyLayoutChange);
new ResizeObserver(notifyLayoutChange).observe(document.querySelector("#workspace-main"));

for (const trigger of launcherTriggers) {
  trigger.addEventListener("click", () => {
    launcherTrigger = trigger;
    launcher.showModal();
    trigger.setAttribute("aria-expanded", "true");
    launcherClose.focus();
  });
}

launcherClose?.addEventListener("click", () => launcher.close());
launcher?.addEventListener("close", () => {
  launcherTrigger?.setAttribute("aria-expanded", "false");
  launcherTrigger?.focus();
});

for (const button of pinButtons) {
  button.addEventListener("click", () => {
    const appId = button.dataset.pinApp;
    if (pinnedIds.has(appId)) pinnedIds.delete(appId);
    else pinnedIds.add(appId);
    savePinnedIds();
    renderPins();
  });
}

window.addEventListener("workspace:pins-sync", (event) => {
  const ids = Array.isArray(event.detail?.pinnedIds) ? event.detail.pinnedIds : [];
  pinnedIds = new Set(ids.filter((id) => registeredIds.has(id)));
  savePinnedIds();
  renderPins();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && body.classList.contains("sidebar-open")) {
    setMobileSidebar(false);
    return;
  }
  if (
    event.key === "Tab"
    && body.classList.contains("sidebar-open")
    && !desktopQuery.matches
  ) {
    const focusable = focusableSidebarElements();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});

desktopQuery.addEventListener("change", (event) => {
  if (event.matches) {
    setMobileSidebar(false, { restoreFocus: false });
    sidebar?.removeAttribute("aria-hidden");
  } else {
    body.classList.remove("sidebar-collapsed");
    sidebar?.setAttribute("aria-hidden", "true");
  }
  notifyLayoutChange();
});

if (desktopQuery.matches) {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    collapsed = false;
  }
  setDesktopSidebar(collapsed);
  sidebar?.removeAttribute("aria-hidden");
} else {
  sidebar?.setAttribute("aria-hidden", "true");
}

renderPins();
