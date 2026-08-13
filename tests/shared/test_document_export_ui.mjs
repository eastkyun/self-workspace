import assert from "node:assert/strict";
import test from "node:test";

import {
  createExportController,
  downloadDocument,
} from "../../shared/static/document-export.js";

function fakeButton(dataset) {
  const listeners = new Map();
  const classes = new Set();
  const attributes = new Map();
  return {
    dataset,
    disabled: false,
    textContent: dataset.exportLabel,
    classList: {
      add: (value) => classes.add(value),
      remove: (value) => classes.delete(value),
      contains: (value) => classes.has(value),
    },
    addEventListener: (name, listener) => listeners.set(name, listener),
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
    hasAttribute: (name) => attributes.has(name),
  };
}

function fakeStatus() {
  const classes = new Set();
  return {
    textContent: "",
    classList: {
      add: (value) => classes.add(value),
      remove: (value) => classes.delete(value),
      contains: (value) => classes.has(value),
    },
  };
}

test("downloads a converted document using the response filename", async () => {
  let clicked = false;
  let revoked = "";
  const link = {
    remove() {},
    click() { clicked = true; },
  };
  const filename = await downloadDocument({
    url: "/export?format=pdf",
    fallbackFilename: "fallback.pdf",
    fetchImpl: async () => new Response(new Blob(["pdf"]), {
      headers: { "content-disposition": "attachment; filename*=UTF-8''%ED%95%9C%EA%B8%80.pdf" },
    }),
    documentRef: {
      body: { append() {} },
      createElement: () => link,
    },
    urlApi: {
      createObjectURL: () => "blob:test",
      revokeObjectURL: (value) => { revoked = value; },
    },
  });

  assert.equal(filename, "한글.pdf");
  assert.equal(clicked, true);
  assert.equal(revoked, "blob:test");
});

test("controller exposes conversion progress and prevents duplicate conversions", async () => {
  const word = fakeButton({ exportUrl: "/word", exportFilename: "note.docx", exportLabel: "Word" });
  const pdf = fakeButton({ exportUrl: "/pdf", exportFilename: "note.pdf", exportLabel: "PDF" });
  const status = fakeStatus();
  let finish;
  let calls = 0;
  const pending = new Promise((resolve) => { finish = resolve; });
  const controller = createExportController({
    buttons: [word, pdf],
    status,
    download: async () => { calls += 1; await pending; },
  });

  const running = controller.run(pdf);
  await Promise.resolve();
  assert.equal(status.textContent, "PDF 변환 중…");
  assert.equal(word.disabled, true);
  assert.equal(pdf.disabled, true);
  assert.equal(pdf.classList.contains("is-loading"), true);
  await controller.run(word);
  assert.equal(calls, 1);

  finish();
  await running;
  assert.equal(status.textContent, "PDF 다운로드를 시작했습니다.");
  assert.equal(word.disabled, false);
  assert.equal(pdf.disabled, false);
  assert.equal(pdf.classList.contains("is-loading"), false);
});

test("controller reports conversion failures", async () => {
  const pdf = fakeButton({ exportUrl: "/pdf", exportLabel: "PDF" });
  const status = fakeStatus();
  const controller = createExportController({
    buttons: [pdf],
    status,
    download: async () => { throw new Error("시간 초과"); },
  });

  await controller.run(pdf);
  assert.equal(status.textContent, "시간 초과");
  assert.equal(status.classList.contains("is-error"), true);
  assert.equal(pdf.disabled, false);
});
