import {
  collectAssetImages,
  createMarkdownRenderer,
  mountMarkdownViewer,
  resolveAssetImages,
} from "../shared/markdown/index.js";

const sourceElement = document.querySelector("#publication-source");
const container = document.querySelector("#publication-markdown");

if (sourceElement && container) {
  const payload = JSON.parse(sourceElement.textContent || "{}");
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
}
