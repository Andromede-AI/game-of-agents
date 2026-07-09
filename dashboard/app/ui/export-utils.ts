"use client";

import { toBlob } from "html-to-image";
import JSZip from "jszip";

type ExportFrameOptions = {
  title: string;
};

function removeIgnoredNodes(node: HTMLElement) {
  node.querySelectorAll("[data-export-ignore='true']").forEach((element) => {
    element.remove();
  });
}

function createExportFrame(node: HTMLElement, options: ExportFrameOptions) {
  const host = document.createElement("div");
  host.className = "export-shell";
  const frame = document.createElement("section");
  frame.className = "export-frame";

  const title = document.createElement("header");
  title.className = "export-frame__title";
  title.textContent = options.title;

  const stamp = document.createElement("div");
  stamp.className = "export-frame__stamp";
  stamp.textContent = new Date().toLocaleString();

  const clone = node.cloneNode(true) as HTMLElement;
  removeIgnoredNodes(clone);

  frame.append(title, stamp, clone);
  host.append(frame);
  document.body.append(host);
  return { host, frame };
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function renderNodeToBlob(node: HTMLElement, title: string) {
  const { host, frame } = createExportFrame(node, { title });
  try {
    const blob = await toBlob(frame, {
      cacheBust: true,
      pixelRatio: 2,
    });
    if (!blob) {
      throw new Error("Export failed while rendering the image.");
    }
    return blob;
  } finally {
    host.remove();
  }
}

export async function exportNodeAsPng(node: HTMLElement, filename: string, title: string) {
  const blob = await renderNodeToBlob(node, title);
  downloadBlob(filename, blob);
}

export async function createZipBundle(entries: Array<{ name: string; data: Blob | string }>) {
  const zip = new JSZip();
  for (const entry of entries) {
    zip.file(entry.name, entry.data);
  }
  return zip.generateAsync({ type: "blob" });
}

export async function exportZipBundle(
  filename: string,
  entries: Array<{ name: string; data: Blob | string }>,
) {
  const blob = await createZipBundle(entries);
  downloadBlob(filename, blob);
}

export async function printNodeAsReport(node: HTMLElement, title: string) {
  const blob = await renderNodeToBlob(node, title);
  const url = URL.createObjectURL(blob);
  const printWindow = window.open("", "_blank", "noopener,noreferrer,width=1280,height=900");
  if (!printWindow) {
    URL.revokeObjectURL(url);
    throw new Error("The browser blocked the report window.");
  }
  printWindow.document.write(`<!doctype html>
<html>
  <head>
    <title>${title}</title>
    <style>
      body {
        margin: 0;
        padding: 24px;
        background: #ffffff;
        font-family: ui-sans-serif, system-ui, sans-serif;
      }
      img {
        display: block;
        width: 100%;
        height: auto;
      }
      @page {
        margin: 0.5in;
      }
    </style>
  </head>
  <body>
    <img src="${url}" alt="${title}" />
  </body>
</html>`);
  printWindow.document.close();
  printWindow.onload = () => {
    printWindow.focus();
    printWindow.print();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      printWindow.close();
    }, 500);
  };
}
