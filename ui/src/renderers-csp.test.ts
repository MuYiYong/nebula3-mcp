// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

const { embed } = vi.hoisted(() => ({
  embed: vi.fn(async (container: HTMLElement) => {
    container.append(document.createElementNS("http://www.w3.org/2000/svg", "svg"));
  }),
}));

vi.mock("vega-embed", () => ({ default: embed }));

import { renderCharts } from "./renderers";

describe("CSP-safe chart rendering", () => {
  beforeEach(() => {
    embed.mockClear();
  });

  it("uses Vega's AST interpreter instead of runtime code generation", async () => {
    const container = document.createElement("section");
    const spec = {
      data: { values: [{ sector: "A", score: 2 }] },
      mark: "bar",
      encoding: {
        x: { field: "sector", type: "nominal" },
        y: { field: "score", type: "quantitative" },
      },
    };

    await renderCharts(container, [
      { format: "vega-lite-v5", description: "Scores by sector", spec },
    ]);

    expect(embed).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      spec,
      expect.objectContaining({ ast: true, renderer: "svg" }),
    );
  });
});
