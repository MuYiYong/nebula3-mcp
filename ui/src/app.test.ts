// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import { mountApp } from "./app";
import type { McpBridge, ToolResultListener } from "./bridge";
import type { GraphElement, GraphElements } from "./types";

const graphMock = vi.hoisted(() => ({
  select: null as ((data: GraphElement["data"]) => void) | null,
  elements: { nodes: [], edges: [] } as GraphElements,
  destroy: vi.fn(),
}));
vi.mock("./renderers", async (original) => ({
  ...await original<typeof import("./renderers")>(),
  createGraphView: (_container: HTMLElement, select: typeof graphMock.select) => {
    graphMock.select = select;
    return { update: (elements: GraphElements) => { graphMock.elements = elements; },
      setLayout: vi.fn(), fit: vi.fn(), reset: vi.fn(), destroy: graphMock.destroy };
  },
}));
function selectElement(id: string): void {
  const item = [...graphMock.elements.nodes, ...graphMock.elements.edges].find(e => e.data.id === id);
  if (!item) throw new Error("Missing element");
  graphMock.select?.(item.data);
}


function fakeBridge(): {
  bridge: McpBridge;
  emitResult: (params: unknown) => void;
  request: ReturnType<typeof vi.fn>;
} {
  const resultListeners = new Set<ToolResultListener>();
  const request = vi.fn(async (): Promise<unknown> => ({}));
  return {
    bridge: {
      connect: vi.fn().mockResolvedValue({ hostContext: { theme: "light" } }),
      request,
      subscribeToToolInput: vi.fn(() => () => undefined),
      subscribeToToolResult: vi.fn((listener: ToolResultListener) => {
        resultListeners.add(listener);
        return () => resultListeners.delete(listener);
      }),
      dispose: vi.fn(),
    },
    emitResult: (params: unknown) => {
      for (const listener of resultListeners) {
        listener(params);
      }
    },
    request,
  };
}

function presentation(statement: string): unknown {
  return {
    structuredContent: {
      result: {
        query: { executed_statement: statement },
        table: { columns: ["name"], rows: [{ name: "A" }] },
        graph: {
          format: "cytoscape-elements-v1",
          space: "demo",
          elements: { nodes: [], edges: [] },
          paths: [],
          truncated: false,
        },
        charts: [],
      },
      explanation: "返回了 1 行。",
    },
  };
}

function graphPresentation(): unknown {
  return {
    structuredContent: {
      result: {
        query: {
          statement: "MATCH (n)-[e]-(m) RETURN n, e, m LIMIT 10",
          executed_statement: "PROFILE MATCH (n)-[e]-(m) RETURN n, e, m LIMIT 10",
          session_statement: "USE `demo`",
          space: "demo",
          read_only: true,
          connection_id: "session-a",
        },
        table: {
          columns: ["n", "e", "m"],
          rows: [],
          returned_row_count: 1,
          result_row_count: 1,
          truncated: false,
        },
        graph: {
          format: "cytoscape-elements-v1",
          space: "demo",
          elements: {
            nodes: [
              {
                data: {
                  id: "demo:1",
                  vid: "1",
                  space: "demo",
                  type: "corp",
                  tags: ["corp"],
                  properties: { "corp.name": "A", "corp.code": "A-1" },
                },
              },
              {
                data: {
                  id: "demo:2",
                  vid: "2",
                  space: "demo",
                  type: "corp",
                  tags: ["corp"],
                  properties: { "corp.name": "B", "corp.code": "B-2" },
                },
              },
            ],
            edges: [
              {
                data: {
                  id: "demo:1:invest:0:2",
                  source: "demo:1",
                  target: "demo:2",
                  src: "1", dst: "2", rank: 0,
                  type: "invest", properties: { year: 2026, weight: 10 },
                },
              },
            ],
          },
          paths: [],
          truncated: false,
        },
        analysis: null,
        charts: [],
        explanation_context: {},
        truncation: { truncated: false, reasons: [] },
      },
      explanation: "A 与 B 之间存在 Invest 关系。",
    },
  };
}

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const item = Array.from(root.querySelectorAll("button")).find(
    (candidate) => candidate.textContent === label,
  );
  if (!(item instanceof HTMLButtonElement)) {
    throw new Error(`Missing button: ${label}`);
  }
  return item;
}

async function flushAsyncWork(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("query-result app shell", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  it("shows exact nGQL and accessible result tabs from a tool result", () => {
    const root = document.createElement("main");
    document.body.append(root);
    const fake = fakeBridge();

    mountApp(root, fake.bridge);
    fake.emitResult(presentation("USE demo;\nMATCH (n) RETURN n LIMIT 10"));

    expect(root.querySelector("code")?.textContent).toBe(
      "USE demo;\nMATCH (n) RETURN n LIMIT 10",
    );
    expect(root.querySelector('[role="tablist"]')?.getAttribute("aria-label")).toBe(
      "查询结果",
    );
    expect(
      Array.from(root.querySelectorAll('[role="tab"]'), (tab) => tab.textContent),
    ).toEqual(["图", "表格", "图表", "PROFILE", "人工解释", "查询记录"]);
    expect(root.querySelector('button[aria-label="复制 nGQL"]')).not.toBeNull();
    expect(root.querySelector(".ngql-header h2")?.textContent).toBe("nGQL");
    expect(root.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe(
      "表格",
    );
  });

  it("renders database strings as text instead of markup", () => {
    const root = document.createElement("main");
    document.body.append(root);
    const fake = fakeBridge();

    mountApp(root, fake.bridge);
    fake.emitResult(presentation('<img src=x onerror="alert(1)">'));

    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelector("code")?.textContent).toBe(
      '<img src=x onerror="alert(1)">',
    );
  });

  it("bounds the diagnostic fallback for malformed tool results", () => {
    const root = document.createElement("main");
    document.body.append(root);
    const fake = fakeBridge();

    mountApp(root, fake.bridge);
    fake.emitResult({ structuredContent: { unexpected: "x".repeat(20_000) } });

    const fallback = root.querySelector('[data-testid="bounded-fallback"]');
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent?.length).toBeLessThanOrEqual(4_100);
  });

  it("changes all layouts, fits, and resets without a database call", () => {
    const root = document.createElement("main");
    document.body.append(root);
    const fake = fakeBridge();

    mountApp(root, fake.bridge);
    fake.emitResult(graphPresentation());
    const select = root.querySelector('select[aria-label="图布局"]');
    if (!(select instanceof HTMLSelectElement)) {
      throw new Error("Missing layout selector");
    }
    for (const layout of ["cose", "breadthfirst", "circle", "grid"]) {
      select.value = layout;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      expect(select.value).toBe(layout);
    }
    button(root, "适应窗口").click();
    button(root, "重置视图").click();

    expect(fake.request).not.toHaveBeenCalled();
  });


});


describe("graph properties and profile presentation", () => {
  it("shows returned identity and properties below the canvas without recreating it", () => {
    const root = document.createElement("main");
    const fake = fakeBridge();
    mountApp(root, fake.bridge);
    fake.emitResult(graphPresentation());
    expect(root.querySelector(".node-list")).toBeNull();
    const canvas = root.querySelector(".graph-canvas");
    const before = graphMock.destroy.mock.calls.length;
    selectElement("demo:1");
    expect(Array.from(root.querySelectorAll(".element-identity dt, .element-identity dd"),
      (item) => item.textContent)).toEqual(["VID", "1", "Tag", "corp"]);
    expect(Array.from(root.querySelectorAll(".element-properties dt, .element-properties dd"),
      (item) => item.textContent)).toEqual(["corp.name", "A", "corp.code", "A-1"]);
    expect(root.textContent).not.toContain("扩展一跳");
    selectElement("demo:1:invest:0:2");
    expect(Array.from(root.querySelectorAll(".element-identity dt, .element-identity dd"),
      (item) => item.textContent)).toEqual(["边类型", "invest", "起点 → 终点", "1 → 2", "rank", "0"]);
    expect(Array.from(root.querySelectorAll(".element-properties dt, .element-properties dd"),
      (item) => item.textContent)).toEqual(["year", "2026", "weight", "10"]);
    expect(graphMock.elements.nodes.map((node) => node.data.label)).toEqual(["A", "B"]);
    expect(fake.request).not.toHaveBeenCalled();
    expect(root.textContent).not.toContain("扩展一跳");
    expect(root.querySelector(".graph-canvas")).toBe(canvas);
    expect(graphMock.destroy.mock.calls.length).toBe(before);
  });

  it("copies displayed nGQL with a compact icon and renders 3.x PROFILE metrics", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
    const root = document.createElement("main");
    const fake = fakeBridge();
    mountApp(root, fake.bridge);
    const data = presentation("PROFILE RETURN 1") as any;
    data.structuredContent.result.query.display_statement = "RETURN 1";
    data.structuredContent.result.profile = { latency_us: 1200, operators: [
      { name: "Project", depth: 0, rows: 1, exec_time_ms: 1.2, total_time_ms: 1.5, executions: 1 },
    ], truncated: false };
    fake.emitResult(data);
    expect(root.querySelector("code")?.textContent).toBe("RETURN 1");
    const button = root.querySelector<HTMLButtonElement>('[aria-label="复制 nGQL"]')!;
    expect(button.querySelector("svg")).not.toBeNull();
    expect(button.closest(".ngql-line")).not.toBeNull();
    button.click();
    expect(copy).toHaveBeenCalledWith("RETURN 1");
    await flushAsyncWork();
    expect(root.querySelector('[role="status"]')?.textContent).toBe("已复制");
    const tab = Array.from(root.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find(t => t.textContent === "PROFILE")!;
    tab.click();
    expect(root.querySelector('[role="tabpanel"]')?.textContent).toContain("Project");
    expect(root.querySelector('[role="tabpanel"]')?.textContent).toContain("1.2");
    expect(root.querySelector('[role="tabpanel"]')?.textContent).toContain("1.5");
    expect(root.querySelector('[role="tabpanel"]')?.textContent).toContain("总计(ms)");
    expect(root.querySelectorAll(".ngql-header")).toHaveLength(1);
    expect(root.textContent).not.toContain("PROFILE JSON");
  });

  it("hides the automatic PROFILE prefix in legacy results without a display statement", () => {
    const root = document.createElement("main");
    const fake = fakeBridge();
    mountApp(root, fake.bridge);
    fake.emitResult(presentation("PROFILE MATCH (v) RETURN v LIMIT 10"));
    expect(root.querySelector(".ngql-header code")?.textContent).toBe("MATCH (v) RETURN v LIMIT 10");
    button(root, "查询记录").click();
    expect(root.querySelector(".result-panel code")?.textContent).toBe("MATCH (v) RETURN v LIMIT 10");
  });

  it("lists every statement of the session and marks the current result", () => {
    const root = document.createElement("main");
    const fake = fakeBridge();
    const data = presentation("PROFILE MATCH (v) RETURN v LIMIT 10") as any;
    data.structuredContent.result.result_id = "r2";
    data.structuredContent.history = [
      { seq: 1, statement: "MATCH (v) RETURN v LIMIT 10", kind: "query", space: null, ok: false,
        code: "-1009", executed_at: "2026-09-27T09:25:01+00:00", result_id: null },
      { seq: 2, statement: "USE `basketballplayer`", kind: "use", space: "basketballplayer",
        ok: true, code: "0", executed_at: "2026-09-27T09:25:30+00:00", result_id: null },
      { seq: 3, statement: "MATCH (v) RETURN v LIMIT 10", kind: "query", space: "basketballplayer",
        ok: true, code: "0", executed_at: "2026-09-27T09:25:31+00:00", result_id: "r2" },
    ];
    mountApp(root, fake.bridge);
    fake.emitResult(data);
    button(root, "查询记录").click();
    const items = Array.from(root.querySelectorAll<HTMLLIElement>(".history-list li"));
    expect(items.map((item) => item.querySelector("code")?.textContent)).toEqual([
      "MATCH (v) RETURN v LIMIT 10", "USE `basketballplayer`", "MATCH (v) RETURN v LIMIT 10",
    ]);
    expect(items[0].textContent).toContain("失败（-1009）");
    expect(items[1].textContent).toContain("切换图空间");
    expect(items[2].getAttribute("aria-current")).toBe("true");
    expect(items.filter((item) => item.hasAttribute("aria-current"))).toHaveLength(1);
  });
});

it("keeps large VIDs and ranks exact and omits internal element IDs", () => {
  const root = document.createElement("main");
  const fake = fakeBridge();
  const data = graphPresentation() as any;
  data.structuredContent.result.graph.elements.nodes[0].data.vid = "9223372036854775807";
  const edge = data.structuredContent.result.graph.elements.edges[0].data;
  edge.rank = "9223372036854775806";
  mountApp(root, fake.bridge);
  fake.emitResult(data);
  selectElement("demo:1");
  expect(root.querySelector(".node-inspector")?.textContent).toBe(
    "VID9223372036854775807Tagcorpcorp.nameAcorp.codeA-1",
  );
  expect(root.querySelector(".node-inspector")?.textContent).not.toContain("demo:1");
  selectElement(edge.id);
  expect(root.querySelector(".node-inspector")?.textContent).toContain("rank9223372036854775806");
  expect(root.querySelector(".node-inspector")?.textContent).not.toContain(edge.id);
});

it("shows a plain empty state when a returned graph element has no properties", () => {
  const root = document.createElement("main");
  const fake = fakeBridge();
  const data = graphPresentation() as any;
  data.structuredContent.result.graph.elements.nodes[0].data.properties = {};
  mountApp(root, fake.bridge);
  fake.emitResult(data);
  selectElement("demo:1");
  expect(root.querySelector(".node-inspector")?.textContent).toBe("VID1Tagcorp查询结果未包含属性");
});

it("keeps returned null and whitespace values intact in clicked results", () => {
  const root = document.createElement("main");
  const fake = fakeBridge();
  const data = graphPresentation() as any;
  data.structuredContent.result.graph.elements.nodes[0].data.properties = {
    name: "A  B\nC", score: null,
  };
  mountApp(root, fake.bridge);
  fake.emitResult(data);
  selectElement("demo:1");
  expect(Array.from(root.querySelectorAll(".element-properties dd"),
    (item) => item.textContent)).toEqual(["A  B\nC", "null"]);
});

it("shows typed date values compactly and preserves explanation paragraphs", () => {
  const root = document.createElement("main");
  const fake = fakeBridge();
  const data = graphPresentation() as any;
  data.structuredContent.result.graph.elements.nodes[0].data.properties.birthday = {
    $type: "datetime", value: "1965-01-01T00:00:00",
  };
  data.structuredContent.explanation = "观察：A 关联 B。\n\n边界：局部样本。";
  mountApp(root, fake.bridge);
  fake.emitResult(data);
  selectElement("demo:1");
  const properties = root.querySelector('[data-testid="node-properties"]')!.textContent!;
  expect(properties).toContain("birthday1965-01-01T00:00:00");
  expect(properties).not.toContain("$type");
  button(root, "人工解释").click();
  expect(Array.from(root.querySelectorAll('[role="tabpanel"] p'), p => p.textContent)).toEqual([
    "观察：A 关联 B。", "边界：局部样本。",
  ]);
});
