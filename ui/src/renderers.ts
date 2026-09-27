import cytoscape, {
  type Core,
  type ElementDefinition,
  type LayoutOptions,
} from "cytoscape";
import vegaEmbed, { type VisualizationSpec } from "vega-embed";

import type { ChartSpec, GraphElement, GraphElements, TableResult } from "./types";

export type GraphLayout = "cose" | "breadthfirst" | "circle" | "grid";

export interface GraphView {
  update(elements: GraphElements): void;
  setLayout(layout: GraphLayout): void;
  fit(): void;
  reset(): void;
  destroy(): void;
}

export function createGraphView(
  container: HTMLElement,
  onSelect: (data: GraphElement["data"]) => void,
): GraphView {
  let currentLayout: GraphLayout = "cose";
  const headless = navigator.userAgent.toLowerCase().includes("jsdom");
  const core: Core = cytoscape({
    container: headless ? undefined : container,
    headless,
    elements: [],
    style: headless
      ? undefined
      : [
          {
            selector: "node",
            style: {
              "background-color": "#4f7cff",
              color: "#17213b",
              label: "data(label)",
              "font-size": 11,
              "text-valign": "bottom",
              "text-margin-y": 7,
            },
          },
          {
            selector: "edge",
            style: {
              width: 2,
              "line-color": "#9aa7c7",
              "target-arrow-color": "#9aa7c7",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: ":selected",
            style: {
              "background-color": "#ff9f43",
              "line-color": "#ff9f43",
              "target-arrow-color": "#ff9f43",
            },
          },
        ],
  });

  core.on("tap", "node, edge", (event) => {
    onSelect(event.target.data() as GraphElement["data"]);
  });

  const runLayout = (): void => {
    if (core.elements().empty()) {
      return;
    }
    core.layout({
      name: currentLayout,
      animate: false,
      fit: true,
      padding: 24,
    } as LayoutOptions).run();
  };

  return {
    update(elements: GraphElements): void {
      core.batch(() => {
        core.elements().remove();
        core.add([...elements.nodes, ...elements.edges] as ElementDefinition[]);
      });
      runLayout();
    },
    setLayout(layout: GraphLayout): void {
      currentLayout = layout;
      runLayout();
    },
    fit(): void {
      core.fit(undefined, 24);
    },
    reset(): void {
      core.reset();
      core.center();
    },
    destroy(): void {
      core.destroy();
    },
  };
}

export function renderTable(container: HTMLElement, table: TableResult): void {
  const element = document.createElement("table");
  const head = document.createElement("thead");
  const headingRow = document.createElement("tr");
  for (const column of table.columns) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = column;
    headingRow.append(cell);
  }
  head.append(headingRow);

  const body = document.createElement("tbody");
  for (const row of table.rows) {
    const tableRow = document.createElement("tr");
    for (const column of table.columns) {
      const cell = document.createElement("td");
      cell.textContent = formatValue(row[column]);
      tableRow.append(cell);
    }
    body.append(tableRow);
  }
  element.append(head, body);
  container.replaceChildren(element);
}

export async function renderCharts(
  container: HTMLElement,
  charts: ChartSpec[],
): Promise<void> {
  container.replaceChildren();
  for (const chart of charts) {
    const figure = document.createElement("figure");
    const view = document.createElement("div");
    const caption = document.createElement("figcaption");
    caption.textContent = chart.description;
    figure.append(view, caption);
    container.append(figure);
    await vegaEmbed(view, chart.spec as VisualizationSpec, {
      actions: false,
      ast: true,
      mode: "vega-lite",
      renderer: "svg",
    });
  }
}

export function renderExplanation(container: HTMLElement, explanation: string): void {
  const paragraphs = explanation.split(/\n\s*\n/u).map(text => {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    return paragraph;
  });
  container.replaceChildren(...paragraphs);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
