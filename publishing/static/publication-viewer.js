import {
  collectAssetImages,
  createMarkdownRenderer,
  mountMarkdownViewer,
  resolveAssetImages,
} from "../shared/markdown/index.js";
import { createExportController } from "../shared/document-export.js";

const sourceElement = document.querySelector("#publication-source");
const container = document.querySelector("#publication-markdown");
const deleteButton = document.querySelector("#publication-delete");
const actionStatus = document.querySelector("#publication-action-status");

if (sourceElement && container) {
  const payload = JSON.parse(sourceElement.textContent || "{}");
  createExportController({
    buttons: document.querySelectorAll(".publication-export-link"),
    status: actionStatus,
  });
  const assetIds = collectAssetImages(payload.markdown || "");
  const assetImageResolutions = assetIds.length
    ? await resolveAssetImages(assetIds, {
      endpoint: payload.assetResolveEndpoint,
    })
    : new Map();
  mountMarkdownViewer(container, payload.markdown || "", {
    renderer: createMarkdownRenderer(),
    renderOptions: {
      ...(payload.renderOptions || {}),
      assetImageResolutions,
    },
    ariaLabel: "발행 문서 본문",
  });

  deleteButton?.addEventListener("click", async () => {
    const confirmed = window.confirm(
      "이 발행물을 공개 목록에서 삭제할까요? 원본 Note와 immutable version은 보관됩니다.",
    );
    if (!confirmed) return;
    deleteButton.disabled = true;
    actionStatus.classList.remove("is-error");
    actionStatus.textContent = "발행물을 보관하는 중…";
    try {
      const response = await fetch(payload.archiveEndpoint, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : "발행물을 삭제하지 못했습니다.",
        );
      }
      window.location.assign(payload.libraryUrl);
    } catch (error) {
      deleteButton.disabled = false;
      actionStatus.textContent = error.message;
      actionStatus.classList.add("is-error");
    }
  });
}
