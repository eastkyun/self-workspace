function dispositionFilename(headerValue) {
  if (!headerValue) return "";
  const encoded = headerValue.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return encoded;
    }
  }
  return headerValue.match(/filename="([^"]+)"/i)?.[1] || "";
}

export async function downloadDocument({
  url,
  fallbackFilename,
  fetchImpl = fetch,
  documentRef = document,
  urlApi = URL,
}) {
  const response = await fetchImpl(url, { headers: { Accept: "application/octet-stream" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.message || body.detail || "문서를 변환하지 못했습니다.");
  }

  const blobUrl = urlApi.createObjectURL(await response.blob());
  const link = documentRef.createElement("a");
  link.href = blobUrl;
  link.download = dispositionFilename(response.headers.get("content-disposition")) || fallbackFilename;
  link.hidden = true;
  documentRef.body.append(link);
  try {
    link.click();
    return link.download;
  } finally {
    link.remove();
    urlApi.revokeObjectURL(blobUrl);
  }
}

export function createExportController({ buttons, status, onError = () => {}, download = downloadDocument }) {
  const availableButtons = [...buttons].filter(Boolean);
  let busy = false;

  async function run(button) {
    if (busy || !button?.dataset.exportUrl) return;
    busy = true;
    const label = button.dataset.exportLabel || button.textContent.trim();
    status?.classList.remove("is-error");
    if (status) status.textContent = `${label} 변환 중…`;
    availableButtons.forEach((item) => {
      item.disabled = true;
      item.setAttribute("aria-busy", "true");
    });
    button.classList.add("is-loading");

    try {
      await download({
        url: button.dataset.exportUrl,
        fallbackFilename: button.dataset.exportFilename || `document.${label.toLowerCase()}`,
      });
      if (status) status.textContent = `${label} 다운로드를 시작했습니다.`;
    } catch (error) {
      const message = error instanceof Error ? error.message : "문서를 변환하지 못했습니다.";
      if (status) {
        status.textContent = message;
        status.classList.add("is-error");
      }
      onError(error);
    } finally {
      availableButtons.forEach((item) => {
        item.disabled = false;
        item.removeAttribute("aria-busy");
      });
      button.classList.remove("is-loading");
      busy = false;
    }
  }

  availableButtons.forEach((button) => button.addEventListener("click", () => run(button)));
  return { run };
}
