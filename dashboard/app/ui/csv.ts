export type CsvValue = string | number | boolean | null | undefined;

function escapeCsvValue(value: CsvValue) {
  if (value === null || value === undefined) {
    return "";
  }
  const stringValue = String(value);
  if (/[",\n]/.test(stringValue)) {
    return `"${stringValue.replaceAll('"', '""')}"`;
  }
  return stringValue;
}

export function serializeCsv(
  rows: Array<Record<string, CsvValue>>,
  columns?: string[],
) {
  const header = columns ?? Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const lines = [header.map(escapeCsvValue).join(",")];
  for (const row of rows) {
    lines.push(header.map((column) => escapeCsvValue(row[column])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

export function downloadCsv(
  filename: string,
  rows: Array<Record<string, CsvValue>>,
  columns?: string[],
) {
  const blob = new Blob([serializeCsv(rows, columns)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
