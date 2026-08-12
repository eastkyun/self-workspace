const root = document.querySelector("[data-workspace-search]");

const text = (value) => document.createTextNode(value == null ? "" : String(value));

function resultGroup(group) {
  const section = document.createElement("section");
  section.className = "workspace-search-group";
  const title = document.createElement("h3");
  title.append(text(group.app_key));
  section.append(title);
  const list = document.createElement("ul");
  list.className = "workspace-search-list";
  for (const item of group.results) {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = item.url;
    const heading = document.createElement("strong");
    heading.append(text(item.title));
    const summary = document.createElement("span");
    summary.append(text(item.summary));
    link.append(heading, summary);
    row.append(link);
    list.append(row);
  }
  section.append(list);
  return section;
}

if (root) {
  const form = root.querySelector("[data-search-form]");
  const input = root.querySelector("[data-search-query]");
  const status = root.querySelector("[data-search-status]");
  const results = root.querySelector("[data-search-results]");
  const workspaceId = root.dataset.workspaceId;

  const run = async (query) => {
    results.replaceChildren();
    if (!query.trim()) {
      status.textContent = "검색어를 입력하세요.";
      return;
    }
    status.textContent = "검색 중…";
    const url = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/search?q=${encodeURIComponent(query)}`;
    try {
      const response = await fetch(url, {headers: {Accept: "application/json"}});
      if (!response.ok) throw new Error(`search failed: ${response.status}`);
      const payload = await response.json();
      const count = payload.groups.reduce((total, group) => total + group.results.length, 0);
      status.textContent = count ? `${count}개의 결과를 찾았습니다.` : "검색 결과가 없습니다.";
      results.append(...payload.groups.filter((group) => group.results.length).map(resultGroup));
      if (payload.failures.length) {
        const warning = document.createElement("p");
        warning.className = "workspace-search-warning";
        warning.append(text("일부 앱의 결과를 불러오지 못했습니다."));
        results.append(warning);
      }
    } catch {
      status.textContent = "검색 결과를 불러오지 못했습니다.";
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = input.value.trim();
    const url = new URL(window.location.href);
    query ? url.searchParams.set("q", query) : url.searchParams.delete("q");
    history.replaceState(null, "", url);
    run(query);
  });
  const initial = new URL(window.location.href).searchParams.get("q") || "";
  input.value = initial;
  if (initial) run(initial);
}
