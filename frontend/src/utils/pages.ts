export type PageMode = "all" | "range" | "custom";

export interface PageSelectionValue {
  mode: PageMode;
  start: number;
  end: number;
  selected: number[];
}

export function clampPage(value: number, pageCount: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(Math.trunc(value), 1), Math.max(1, pageCount));
}

export function resolvePageSelection(
  selection: PageSelectionValue,
  pageCount: number,
): number[] {
  const total = Math.max(1, Math.trunc(pageCount));
  if (selection.mode === "all") {
    return Array.from({ length: total }, (_, index) => index + 1);
  }
  if (selection.mode === "range") {
    const start = clampPage(selection.start, total);
    const end = clampPage(selection.end, total);
    const from = Math.min(start, end);
    const to = Math.max(start, end);
    return Array.from({ length: to - from + 1 }, (_, index) => from + index);
  }
  return [...new Set(selection.selected.map((page) => clampPage(page, total)))].sort(
    (a, b) => a - b,
  );
}

export function validatePageSelection(
  selection: PageSelectionValue,
  pageCount: number,
): string | null {
  if (selection.mode === "custom" && selection.selected.length === 0) {
    return "Select at least one page.";
  }
  if (selection.mode === "range") {
    if (!Number.isFinite(selection.start) || !Number.isFinite(selection.end)) {
      return "Enter a valid page range.";
    }
    if (
      selection.start < 1 ||
      selection.end < 1 ||
      selection.start > pageCount ||
      selection.end > pageCount
    ) {
      return `Pages must be between 1 and ${pageCount}.`;
    }
    if (Math.abs(selection.end - selection.start) + 1 > 2000) {
      return "Select at most 2000 pages.";
    }
  }
  if (resolvePageSelection(selection, pageCount).length > 2000) {
    return "Select at most 2000 pages.";
  }
  return null;
}

export function parsePageList(input: string, pageCount: number): number[] {
  const pages = new Set<number>();
  for (const rawPart of input.split(",")) {
    const part = rawPart.trim();
    if (!part) continue;
    const range = part.match(/^(\d+)\s*-\s*(\d+)$/);
    if (range) {
      const start = clampPage(Number(range[1]), pageCount);
      const end = clampPage(Number(range[2]), pageCount);
      const from = Math.min(start, end);
      const to = Math.max(start, end);
      for (let page = from; page <= to && pages.size < 2000; page += 1) pages.add(page);
      continue;
    }
    const page = Number(part);
    if (Number.isInteger(page)) pages.add(clampPage(page, pageCount));
  }
  return [...pages].sort((a, b) => a - b);
}

export function pageWindow(
  pageCount: number,
  anchor: number,
  size = 24,
): { pages: number[]; start: number; end: number; hasPrev: boolean; hasNext: boolean } {
  const total = Math.max(1, pageCount);
  const windowSize = Math.max(1, size);
  const pages = Math.min(windowSize, total);
  const clampedAnchor = clampPage(anchor, total);
  let start = Math.max(1, clampedAnchor - Math.floor(pages / 2));
  if (start + pages - 1 > total) start = Math.max(1, total - pages + 1);
  const end = Math.min(total, start + pages - 1);
  return {
    pages: Array.from({ length: end - start + 1 }, (_, index) => start + index),
    start,
    end,
    hasPrev: start > 1,
    hasNext: end < total,
  };
}
