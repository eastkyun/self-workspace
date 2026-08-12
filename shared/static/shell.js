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
const profileDialog = document.querySelector("#workspace-profile-dialog");
const appLoading = document.querySelector("#workspace-app-loading");
const workspaceMain = document.querySelector("#workspace-main");
let appLoadingFallback = null;

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
    accountAvatar.textContent = display.trim().charAt(0).toUpperCase() || "U";
    accountName.textContent = display;
    accountEmail.textContent = user.email;
    document.querySelector("#workspace-profile-name").textContent = display;
    document.querySelector("#workspace-profile-email").textContent = user.email;
    rewriteWorkspaceAppLinks(user.last_workspace_id);
    window.dispatchEvent(new CustomEvent("workspace:account-loaded", {
      detail: { workspaceId: user.last_workspace_id },
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
document.querySelector("#workspace-profile-open")?.addEventListener("click", () => {
  accountMenu.hidden = true;
  accountToggle.setAttribute("aria-expanded", "false");
  profileDialog.showModal();
});
document.querySelector("#workspace-profile-close")?.addEventListener("click", () => profileDialog.close());
document.querySelector("#workspace-logout")?.addEventListener("click", async () => {
  const response = await fetch(new URL("api/v1/auth/logout", appRoot), {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken() },
  });
  if (response.ok) window.location.assign(new URL("login", appRoot));
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
