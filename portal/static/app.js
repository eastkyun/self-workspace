import { decodePinnedIds } from "./hub-state.js";

const STORAGE_KEY = "my-workspace:pinned-apps:v1";
const sidebar = document.querySelector("#sidebar");
const sidebarOpen = document.querySelector("#sidebar-open");
const sidebarClose = document.querySelector("#sidebar-close");
const sidebarScrim = document.querySelector("#sidebar-scrim");
const launcher = document.querySelector("#launcher");
const launcherToggle = document.querySelector("#launcher-toggle");
const launcherClose = document.querySelector("#launcher-close");
const cardTemplate = document.querySelector("#app-card-template");
const pinnedContainer = document.querySelector("#pinned-apps");
const pinnedEmpty = document.querySelector("#pinned-empty");
const allAppsContainer = document.querySelector("#all-apps");
const sidebarApps = document.querySelector("#sidebar-apps");
const launcherGrid = document.querySelector("#launcher-grid");

let apps = [];
let pinnedIds = new Set();

function safePinnedIds(registeredApps) {
  try {
    return new Set(decodePinnedIds(localStorage.getItem(STORAGE_KEY), registeredApps));
  } catch {
    // Use registry defaults when storage is unavailable or corrupt.
    return new Set(decodePinnedIds(null, registeredApps));
  }
}

function savePinnedIds() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...pinnedIds]));
  } catch {
    // The current page remains usable when storage is disabled.
  }
}

function linkAttributes(link, app) {
  link.href = app.url;
  if (app.kind === "external") {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.external = "true";
  }
}

function appCard(app) {
  const fragment = cardTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".app-card");
  const link = fragment.querySelector(".app-card-link");
  const icon = fragment.querySelector(".app-icon");
  const pinButton = fragment.querySelector(".pin-button");

  card.dataset.appId = app.id;
  linkAttributes(link, app);
  icon.src = app.icon;
  icon.alt = "";
  fragment.querySelector(".app-name").textContent = app.name;
  fragment.querySelector(".app-description").textContent = app.description;
  pinButton.textContent = pinnedIds.has(app.id) ? "고정 해제" : "고정";
  pinButton.setAttribute("aria-pressed", String(pinnedIds.has(app.id)));
  pinButton.setAttribute("aria-label", `${app.name} ${pinButton.textContent}`);
  pinButton.addEventListener("click", () => togglePin(app.id));
  return fragment;
}

function sidebarLink(app) {
  const link = document.createElement("a");
  link.className = "sidebar-link";
  linkAttributes(link, app);
  const image = document.createElement("img");
  image.src = app.icon;
  image.alt = "";
  link.append(image, document.createTextNode(app.name));
  return link;
}

function launcherItem(app) {
  const wrapper = document.createElement("div");
  wrapper.className = "launcher-item";
  const link = document.createElement("a");
  linkAttributes(link, app);
  const image = document.createElement("img");
  image.src = app.icon;
  image.alt = "";
  const label = document.createElement("span");
  label.textContent = app.name;
  link.append(image, label);
  const pin = document.createElement("button");
  pin.type = "button";
  pin.textContent = pinnedIds.has(app.id) ? "★" : "☆";
  pin.setAttribute("aria-label", `${app.name} ${pinnedIds.has(app.id) ? "고정 해제" : "고정"}`);
  pin.addEventListener("click", () => togglePin(app.id));
  wrapper.append(link, pin);
  return wrapper;
}

function render() {
  sidebarApps.replaceChildren(...apps.map(sidebarLink));
  allAppsContainer.replaceChildren(...apps.map(appCard));
  const pinnedApps = apps.filter((app) => pinnedIds.has(app.id));
  pinnedContainer.replaceChildren(...pinnedApps.map(appCard));
  pinnedEmpty.hidden = pinnedApps.length > 0;
  launcherGrid.replaceChildren(...apps.map(launcherItem));
}

function togglePin(appId) {
  if (pinnedIds.has(appId)) pinnedIds.delete(appId);
  else pinnedIds.add(appId);
  savePinnedIds();
  render();
}

function setSidebar(open) {
  document.body.classList.toggle("sidebar-open", open);
  sidebarOpen.setAttribute("aria-expanded", String(open));
  if (open) sidebarClose.focus();
  else sidebarOpen.focus();
}

sidebarOpen.addEventListener("click", () => setSidebar(true));
sidebarClose.addEventListener("click", () => setSidebar(false));
sidebarScrim.addEventListener("click", () => setSidebar(false));
launcherToggle.addEventListener("click", () => {
  launcher.showModal();
  launcherToggle.setAttribute("aria-expanded", "true");
  launcherClose.focus();
});
launcherClose.addEventListener("click", () => launcher.close());
launcher.addEventListener("close", () => {
  launcherToggle.setAttribute("aria-expanded", "false");
  launcherToggle.focus();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) {
    setSidebar(false);
  }
});

async function start() {
  const response = await fetch("/api/hub/apps", {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("앱 목록을 불러오지 못했습니다.");
  apps = await response.json();
  pinnedIds = safePinnedIds(apps);
  render();
}

start().catch((error) => {
  pinnedEmpty.hidden = false;
  pinnedEmpty.textContent = error.message;
});
