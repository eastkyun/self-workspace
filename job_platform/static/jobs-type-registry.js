import { mountWebCollectionEditor } from "../web-collector/web-collection-editor.js";

const editors = new Map([["web-collection", mountWebCollectionEditor]]);

export function mountJobTypeEditor(key, container, options = {}) {
  const mount = editors.get(key);
  if (!mount) throw new Error("지원하지 않는 Job Type 편집기입니다.");
  return mount(container, options);
}
