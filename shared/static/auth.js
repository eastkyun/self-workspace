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
      await submit(form.dataset.endpoint, Object.fromEntries(new FormData(form)));
      window.location.assign(
        form.dataset.endpoint.endsWith("password-reset-requests")
          ? "/login"
          : (form.dataset.successUrl || "/workspace-selector"),
      );
    } catch (error) { showError(error.message); }
  });
  const reset = document.querySelector("[data-reset-form]");
  if (reset) reset.addEventListener("submit", async (event) => {
    event.preventDefault(); showError("");
    const token = new URLSearchParams(window.location.search).get("token");
    try { await submit(`/api/v1/auth/password-resets/${encodeURIComponent(token || "")}`, Object.fromEntries(new FormData(reset))); window.location.assign("/login"); }
    catch (error) { showError(error.message); }
  });
})();
