function imageAlt(file) {
  return String(file?.name || "pasted-image")
    .replace(/\.[^.]+$/u, "")
    .replace(/[\[\]\n\r]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim()
    || "pasted image";
}

export function attachImagePaste(textarea, {
  upload,
  onStatus = () => {},
  onError = () => {},
} = {}) {
  if (!textarea || typeof upload !== "function") {
    throw new TypeError("image paste requires a textarea and upload callback");
  }
  let destroyed = false;

  async function handlePaste(event) {
    const file = [...(event.clipboardData?.items || [])]
      .find((item) => item.kind === "file" && item.type.startsWith("image/"))
      ?.getAsFile();
    if (!file) return;
    event.preventDefault();
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    onStatus("이미지를 업로드하는 중…");
    try {
      const asset = await upload(file);
      if (destroyed) return;
      const token = `![${imageAlt(file)}](image://${asset.id})`;
      textarea.setRangeText(token, start, end, "end");
      textarea.dispatchEvent(new textarea.ownerDocument.defaultView.InputEvent(
        "input",
        { bubbles: true, composed: true, inputType: "insertText", data: token },
      ));
      textarea.focus();
      onStatus("이미지를 본문에 삽입했습니다.");
    } catch (error) {
      if (!destroyed) onError(error);
    }
  }

  textarea.addEventListener("paste", handlePaste);
  return Object.freeze({
    destroy() {
      if (destroyed) return;
      destroyed = true;
      textarea.removeEventListener("paste", handlePaste);
    },
  });
}

export function createAssetApi({
  uploadUrl = "/api/assets/images",
  reconcileUrl = "/api/assets/references/reconcile",
  fetchImpl = globalThis.fetch,
} = {}) {
  async function read(response) {
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(
        typeof payload.detail === "string"
          ? payload.detail
          : `Asset request failed (${response.status})`,
      );
    }
    return payload;
  }

  return Object.freeze({
    async uploadImage(file, uploadSessionId) {
      const url = `${uploadUrl}?upload_session_id=${encodeURIComponent(
        uploadSessionId,
      )}`;
      return read(await fetchImpl(url, {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name || "pasted-image"),
        },
        body: file,
      }));
    },
    async reconcile(owner) {
      return read(await fetchImpl(reconcileUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(owner),
      }));
    },
  });
}
