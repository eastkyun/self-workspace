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
