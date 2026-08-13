(() => {
  const errorNode = document.querySelector("[data-auth-error]");
  const showError = (message) => { if (errorNode) errorNode.textContent = message; };
  const submit = async (endpoint, payload) => {
    const response = await fetch(endpoint, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    if (!response.ok) throw new Error("Request failed. Please try again.");
    return response;
  };
  const form = document.querySelector("[data-auth-form]");
  if (form) form.addEventListener("submit", async (event) => {
    event.preventDefault(); showError("");
    try {
      const response = await submit(form.dataset.endpoint, Object.fromEntries(new FormData(form)));
      const body = await response.json().catch(() => ({}));
      const requestedTarget = form.dataset.successUrl;
      const workspaceTarget = requestedTarget && requestedTarget !== "/workspace-selector"
        ? requestedTarget
        : (body.workspace_id ? `/w/${encodeURIComponent(body.workspace_id)}/home` : "/workspace-selector");
      window.location.assign(
        form.dataset.endpoint.endsWith("password-reset-requests")
          ? "/login"
          : workspaceTarget,
      );
    } catch (error) { showError(error.message); }
  });
  document.querySelectorAll("[data-password-toggle]").forEach((button) => button.addEventListener("click", () => {
    const input = button.closest(".auth-password-field")?.querySelector("input");
    if (!input) return;
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    button.textContent = visible ? "보기" : "숨기기";
    button.setAttribute("aria-label", visible ? "비밀번호 표시" : "비밀번호 숨기기");
  }));
  const reset = document.querySelector("[data-reset-form]");
  if (reset) reset.addEventListener("submit", async (event) => {
    event.preventDefault(); showError("");
    const token = new URLSearchParams(window.location.search).get("token");
    try { await submit(`/api/v1/auth/password-resets/${encodeURIComponent(token || "")}`, Object.fromEntries(new FormData(reset))); window.location.assign("/login"); }
    catch (error) { showError(error.message); }
  });
})();
