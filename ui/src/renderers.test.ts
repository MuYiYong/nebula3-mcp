// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import { renderCharts, renderExplanation, renderTable } from "./renderers";

describe("result renderers", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  it("renders table columns in server order and values as text", () => {
    const container = document.createElement("section");

    renderTable(container, {
      columns: ["name", "score"],
      rows: [{ score: 2, name: "<script>alert(1)</script>" }],
      returned_row_count: 1,
      result_row_count: 1,
      truncated: false,
    });

    expect(
      Array.from(container.querySelectorAll("th"), (cell) => cell.textContent),
    ).toEqual(["name", "score"]);
    expect(
      Array.from(container.querySelectorAll("td"), (cell) => cell.textContent),
    ).toEqual(["<script>alert(1)</script>", "2"]);
    expect(container.querySelector("script")).toBeNull();
  });

  it("renders the human explanation as plain text", () => {
    const container = document.createElement("section");

    renderExplanation(container, "<strong>返回 1 行</strong>");

    expect(container.textContent).toBe("<strong>返回 1 行</strong>");
    expect(container.querySelector("strong")).toBeNull();
  });

  it("renders every Vega-Lite chart as SVG", async () => {
    const container = document.createElement("section");

    await renderCharts(container, [
      {
        format: "vega-lite-v5",
        description: "Scores by sector",
        spec: {
          data: { values: [{ sector: "A", score: 2 }] },
          mark: "bar",
          encoding: {
            x: { field: "sector", type: "nominal" },
            y: { field: "score", type: "quantitative" },
          },
        },
      },
      {
        format: "vega-lite-v5",
        description: "Score distribution",
        spec: {
          data: { values: [{ score: 2 }] },
          mark: "point",
          encoding: { x: { field: "score", type: "quantitative" } },
        },
      },
    ]);

    expect(container.querySelectorAll("figure")).toHaveLength(2);
    expect(container.querySelectorAll("svg")).toHaveLength(2);
    expect(
      Array.from(container.querySelectorAll("figcaption"), (item) => item.textContent),
    ).toEqual(["Scores by sector", "Score distribution"]);
  });
});
