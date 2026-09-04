import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const tokens = readFileSync(resolve(process.cwd(), "src-vue/styles/tokens.css"), "utf8");
const components = readFileSync(resolve(process.cwd(), "src-vue/styles/components.css"), "utf8");
const layout = readFileSync(resolve(process.cwd(), "src-vue/styles/layout.css"), "utf8");

function themeBlock(selector: string): string {
  const start = tokens.indexOf(selector);
  const nextStart = tokens.indexOf("\n:root", start + selector.length);
  return tokens.slice(start, nextStart === -1 ? undefined : nextStart);
}

describe("三主题前景色 token", () => {
  it("分别提供 surface、control、selected、danger、success 前景色", () => {
    for (const selector of [
      ":root,\n:root[data-theme=\"minimal\"][data-effective-scheme=\"light\"]",
      ":root[data-theme=\"minimal\"][data-effective-scheme=\"dark\"]",
      ":root[data-theme=\"schale\"]",
      ":root[data-theme=\"prts\"]",
    ]) {
      const block = themeBlock(selector);
      for (const token of ["--color-surface-ink", "--color-control-ink", "--color-selected-ink", "--color-danger-ink", "--color-success-ink"]) {
        expect(block, `${selector} 缺少 ${token}`).toContain(token);
      }
    }
  });

  it("Schale 与 PRTS 的选中态使用各自可读的 token，而非硬编码前景色", () => {
    expect(themeBlock(":root[data-theme=\"schale\"]")).toContain("--color-selected-ink: #075985");
    expect(themeBlock(":root[data-theme=\"prts\"]")).toContain("--color-selected-ink: #fff4dc");
    expect(components).toContain("color: var(--selected-ink)");
    expect(layout).toContain("color: var(--selected-ink)");
    expect(layout).toContain("color: var(--signal-ink)");
  });
});
