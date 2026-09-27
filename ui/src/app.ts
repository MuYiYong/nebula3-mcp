import { createBridge, type McpBridge } from "./bridge";
import {
  createGraphView,
  type GraphLayout,
  type GraphView,
  renderCharts,
  renderExplanation,
  renderTable,
} from "./renderers";
import "./styles.css";
import type {
  ChartSpec,
  GraphElement,
  GraphElements,
  GraphSpec,
  QueryPresentation,
  QueryOutput,
  ProfileOutput,
  TableResult,
} from "./types";

const TAB_LABELS = ["图", "表格", "图表", "PROFILE", "人工解释", "查询记录"] as const;
type TabLabel = (typeof TAB_LABELS)[number];

interface AppState {
  presentation: QueryPresentation;
  graphElements: GraphElements;
  selectedTab: TabLabel;
  selectedElementId: string | null;
  layout: GraphLayout;
  ngqlHistory: string[];
  profiles: (ProfileOutput | null | undefined)[];
  graphView: GraphView | null;
}

export interface AppController {
  destroy(): void;
}

export function mountApp(root: HTMLElement, bridge: McpBridge): AppController {
  let state: AppState | null = null;
  root.textContent = "正在加载查询结果…";

  const stopInput = bridge.subscribeToToolInput(() => undefined);
  const stopResult = bridge.subscribeToToolResult((params) => {
    const presentation = parsePresentation(params);
    if (presentation === null) {
      renderFallback(root, params);
      return;
    }
    const elements = presentation.result.graph?.elements ?? { nodes: [], edges: [] };
    state?.graphView?.destroy();
    state = {
      presentation,
      graphElements: elements,
      selectedTab: hasGraphElements(elements) ? "图" : "表格",
      selectedElementId: null,
      layout: "cose",
      ngqlHistory: [displayNgql(presentation.result.query)],
      profiles: [presentation.result.profile],
      graphView: null,
    };
    render();
  });
  void bridge.connect().catch((error: unknown) => renderFallback(root, error));

  const render = (): void => {
    if (state === null) {
      return;
    }
    state.graphView?.destroy();
    state.graphView = null;

    const article = document.createElement("article");
    article.className = "query-result";
    article.append(renderNgqlHeader(displayNgql(state.presentation.result.query)));
    const metadata = document.createElement("div");
    metadata.className = "result-metadata";
    if (state.presentation.result.query.environment) {
      const environment = document.createElement("span");
      environment.textContent = `环境：${state.presentation.result.query.environment}`;
      metadata.append(environment);
    }
    if (state.presentation.result.query.space) {
      const space = document.createElement("span");
      space.textContent = `图空间：${state.presentation.result.query.space}`;
      metadata.append(space);
    }
    if (
      state.presentation.result.truncation.truncated ||
      state.presentation.result.graph?.truncated
    ) {
      const marker = document.createElement("span");
      marker.className = "truncation-marker";
      marker.textContent = "结果已截断";
      metadata.append(marker);
    }
    if (metadata.childElementCount > 0) article.append(metadata);

    const tabs = document.createElement("nav");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "查询结果");
    for (const label of TAB_LABELS) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(state.selectedTab === label));
      tab.textContent = label;
      tab.addEventListener("click", () => {
        if (state !== null) {
          state.selectedTab = label;
          render();
        }
      });
      tabs.append(tab);
    }
    article.append(tabs);

    const panel = document.createElement("section");
    panel.className = "result-panel";
    panel.setAttribute("role", "tabpanel");
    let graphContainer: HTMLElement | null = null;
    if (state.selectedTab === "图") {
      graphContainer = renderGraphPanel(panel, state);
    } else if (state.selectedTab === "表格") {
      renderTable(panel, state.presentation.result.table);
    } else if (state.selectedTab === "图表") {
      void renderCharts(panel, state.presentation.result.charts).catch(() => {
        const error = document.createElement("p");
        error.setAttribute("role", "alert");
        error.textContent = "图表渲染失败。";
        panel.replaceChildren(error);
      });
    } else if (state.selectedTab === "PROFILE") {
      state.profiles.forEach((profile) => {
        const entry = document.createElement("section");
        entry.className = "profile-entry";
        renderProfile(entry, profile);
        panel.append(entry);
      });
    } else if (state.selectedTab === "人工解释") {
      renderExplanation(panel, state.presentation.explanation);
    } else {
      renderHistory(panel, state.ngqlHistory);
    }
    article.append(panel);
    root.replaceChildren(article);

    if (graphContainer !== null) {
      state.graphView = createGraphView(graphContainer, (data) => selectElement(data.id));
      state.graphView.update(withGraphLabels(state.graphElements));
      state.graphView.setLayout(state.layout);
    }
  };

  const selectElement = (elementId: string): void => {
    if (state === null) return;
    const element = [...state.graphElements.nodes, ...state.graphElements.edges]
      .find((item) => item.data.id === elementId);
    if (element === undefined) return;
    state.selectedElementId = elementId;
    const panel = root.querySelector<HTMLElement>(".result-panel");
    panel?.querySelector(".node-inspector")?.remove();
    panel?.querySelector('[role="alert"]')?.remove();
    panel?.append(renderNodeInspector(element));
  };

  const renderGraphPanel = (panel: HTMLElement, current: AppState): HTMLElement => {
    const controls = document.createElement("div");
    controls.className = "graph-controls";
    const layout = document.createElement("select");
    layout.setAttribute("aria-label", "图布局");
    for (const [value, label] of [
      ["cose", "自动布局"],
      ["breadthfirst", "层级"],
      ["circle", "环形"],
      ["grid", "网格"],
    ] as const) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      layout.append(option);
    }
    layout.value = current.layout;
    layout.addEventListener("change", () => {
      if (state !== null && isGraphLayout(layout.value)) {
        state.layout = layout.value;
        state.graphView?.setLayout(state.layout);
      }
    });

    const fit = actionButton("适应窗口", () => state?.graphView?.fit());
    const reset = actionButton("重置视图", () => state?.graphView?.reset());
    controls.append(layout, fit, reset);

    const graphContainer = document.createElement("div");
    graphContainer.className = "graph-canvas";
    graphContainer.setAttribute("aria-label", "查询结果图");

    panel.append(controls, graphContainer);
    const selected = [...current.graphElements.nodes, ...current.graphElements.edges].find(
      (node) => node.data.id === current.selectedElementId,
    );
    if (selected !== undefined) {
      panel.append(renderNodeInspector(selected));
    }
    return graphContainer;
  };

  const renderNodeInspector = (node: GraphElement): HTMLElement => {
    const inspector = document.createElement("aside");
    inspector.className = "node-inspector";
    inspector.setAttribute("aria-label", "选中元素的查询结果");
    const identity = elementIdentity(node);
    if (identity.length > 0) {
      const summary = document.createElement("dl");
      summary.className = "element-identity";
      summary.dataset.testid = "element-identity";
      for (const [key, value] of identity) {
        const label = document.createElement("dt");
        label.textContent = key;
        const result = document.createElement("dd");
        result.textContent = value;
        summary.append(label, result);
      }
      inspector.append(summary);
    }
    const properties = document.createElement("dl");
    properties.className = "element-properties";
    properties.dataset.testid = "node-properties";
    if (isRecord(node.data.properties)) {
      for (const [key, value] of Object.entries(node.data.properties)) {
        const label = document.createElement("dt");
        label.textContent = key;
        const result = document.createElement("dd");
        result.textContent = compactValue(value);
        properties.append(label, result);
      }
    }
    if (properties.childElementCount === 0) {
      const empty = document.createElement("p");
      empty.textContent = "查询结果未包含属性";
      inspector.append(empty);
    } else {
      inspector.append(properties);
    }
    return inspector;
  };

  return {
    destroy(): void {
      state?.graphView?.destroy();
      stopInput();
      stopResult();
      bridge.dispose();
    },
  };
}

function renderNgqlHeader(statement: string): HTMLElement {
  const section = document.createElement("section");
  section.className = "ngql-header";
  const heading = document.createElement("h2");
  heading.textContent = "nGQL";
  const line = document.createElement("div");
  line.className = "ngql-line";
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = statement;
  pre.append(code);
  const copy = document.createElement("button");
  copy.type = "button";
  copy.setAttribute("aria-label", "复制 nGQL");
  copy.className = "copy-ngql";
  copy.title = "复制 nGQL";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M9 5V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-1M4 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z");
  svg.append(path);
  copy.append(svg);
  const feedback = document.createElement("span");
  feedback.className = "copy-feedback";
  feedback.setAttribute("role", "status");
  feedback.setAttribute("aria-live", "polite");
  copy.addEventListener("click", () => {
    feedback.textContent = "";
    if (!navigator.clipboard) {
      feedback.textContent = "无法访问剪贴板，请手动选择 nGQL";
      return;
    }
    void navigator.clipboard.writeText(statement).then(
      () => { feedback.textContent = "已复制"; },
      () => { feedback.textContent = "复制失败，请手动选择 nGQL"; },
    );
  });
  line.append(pre, feedback, copy);
  section.append(heading, line);
  return section;
}

function elementIdentity(element: GraphElement): [string, string][] {
  const data = element.data;
  if (typeof data.source === "string" && typeof data.target === "string") {
    const rows: [string, string][] = [];
    if (typeof data.type === "string") rows.push(["边类型", data.type]);
    rows.push(["起点 → 终点", `${compactValue(data.src)} → ${compactValue(data.dst)}`]);
    rows.push(["rank", compactValue(data.rank ?? 0)]);
    return rows;
  }
  const rows: [string, string][] = [];
  if (data.vid !== undefined) rows.push(["VID", compactValue(data.vid)]);
  if (Array.isArray(data.tags) && data.tags.length > 0) {
    rows.push(["Tag", data.tags.filter((tag) => typeof tag === "string").join(", ")]);
  }
  return rows;
}

function renderHistory(container: HTMLElement, statements: string[]): void {
  const list = document.createElement("ol");
  for (const statement of statements) {
    const item = document.createElement("li");
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = statement;
    pre.append(code);
    item.append(pre);
    list.append(item);
  }
  container.replaceChildren(list);
}

function actionButton(label: string, action: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function parsePresentation(params: unknown): QueryPresentation | null {
  if (!isRecord(params) || !isRecord(params.structuredContent)) {
    return null;
  }
  const content = params.structuredContent;
  if (!isRecord(content.result) || typeof content.explanation !== "string") {
    return null;
  }
  const result = content.result;
  if (!isRecord(result.query) || typeof result.query.executed_statement !== "string") {
    return null;
  }
  const table = normalizeTable(result.table);
  const graph = normalizeGraph(result.graph);
  if (table === null || graph === undefined) {
    return null;
  }
  return {
    result: {
      query: {
        statement: typeof result.query.statement === "string" ? result.query.statement : "",
        executed_statement: result.query.executed_statement,
        display_statement: typeof result.query.display_statement === "string" ? result.query.display_statement : undefined,
        connection_id: typeof result.query.connection_id === "string" ? result.query.connection_id : undefined,
        environment: typeof result.query.environment === "string" ? result.query.environment : undefined,
        space:
          typeof result.query.space === "string"
            ? result.query.space
            : graph?.space ?? null,
        session_statement: typeof result.query.session_statement === "string" ? result.query.session_statement : undefined,
        read_only: result.query.read_only !== false,
      },
      table,
      profile: normalizeProfile(result.profile),
      graph,
      analysis: isRecord(result.analysis) ? result.analysis : null,
      charts: normalizeCharts(result.charts),
      explanation_context: isRecord(result.explanation_context)
        ? result.explanation_context
        : {},
      truncation: normalizeTruncation(result.truncation),
    },
    explanation: content.explanation,
  };
}

function normalizeTable(value: unknown): TableResult | null {
  if (!isRecord(value) || !Array.isArray(value.columns) || !Array.isArray(value.rows)) {
    return null;
  }
  const columns = value.columns.filter((item): item is string => typeof item === "string");
  const rows = value.rows.filter(isRecord);
  return {
    columns,
    rows,
    returned_row_count:
      typeof value.returned_row_count === "number" ? value.returned_row_count : rows.length,
    result_row_count:
      typeof value.result_row_count === "number" ? value.result_row_count : null,
    truncated: value.truncated === true,
  };
}

function normalizeGraph(value: unknown): GraphSpec | null | undefined {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    value.format !== "cytoscape-elements-v1" ||
    !isRecord(value.elements) ||
    !Array.isArray(value.elements.nodes) ||
    !Array.isArray(value.elements.edges)
  ) {
    return undefined;
  }
  return {
    format: "cytoscape-elements-v1",
    space: typeof value.space === "string" ? value.space : null,
    elements: {
      nodes: normalizeElements(value.elements.nodes),
      edges: normalizeElements(value.elements.edges),
    },
    paths: Array.isArray(value.paths) ? value.paths.filter(isRecord) : [],
    truncated: value.truncated === true,
  };
}

function normalizeElements(values: unknown[]): GraphElement[] {
  const elements: GraphElement[] = [];
  for (const value of values) {
    if (isRecord(value) && isRecord(value.data) && typeof value.data.id === "string") {
      elements.push({ data: { ...value.data, id: value.data.id } });
    }
  }
  return elements;
}

function normalizeCharts(value: unknown): ChartSpec[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (
      !isRecord(item) ||
      item.format !== "vega-lite-v5" ||
      typeof item.description !== "string" ||
      !isRecord(item.spec)
    ) {
      return [];
    }
    return [
      {
        format: "vega-lite-v5" as const,
        description: item.description,
        spec: item.spec,
      },
    ];
  });
}

function normalizeTruncation(value: unknown): { truncated: boolean; reasons: string[] } {
  if (!isRecord(value)) {
    return { truncated: false, reasons: [] };
  }
  return {
    truncated: value.truncated === true,
    reasons: Array.isArray(value.reasons)
      ? value.reasons.filter((item): item is string => typeof item === "string")
      : [],
  };
}

function withGraphLabels(elements: GraphElements): GraphElements {
  return {
    nodes: elements.nodes.map((node) => ({
      data: { ...node.data, label: graphNodeLabel(node) },
    })),
    edges: elements.edges,
  };
}

function graphNodeLabel(node: GraphElement): string {
  // NebulaGraph 3.x properties are flattened as "<tag>.<property>".
  if (isRecord(node.data.properties)) {
    const entries = Object.entries(node.data.properties);
    for (const key of ["name", "title"]) {
      const match = entries.find(([name]) => name === key || name.endsWith(`.${key}`));
      const value = match?.[1];
      if (typeof value === "string" || typeof value === "number") {
        return String(value);
      }
    }
  }
  if (typeof node.data.vid === "string" || typeof node.data.vid === "number") {
    return String(node.data.vid);
  }
  return typeof node.data.type === "string" ? node.data.type : node.data.id;
}

function compactValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "";
  if (isRecord(value) && ["datetime", "date", "time"].includes(String(value.$type)) && typeof value.value === "string") return value.value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function hasGraphElements(elements: GraphElements): boolean {
  return elements.nodes.length > 0 || elements.edges.length > 0;
}

function isGraphLayout(value: string): value is GraphLayout {
  return value === "cose" || value === "breadthfirst" || value === "circle" || value === "grid";
}

function displayNgql(query: QueryOutput["query"]): string {
  return query.display_statement ?? query.executed_statement.replace(/^\s*PROFILE\s+/iu, "");
}

function normalizeProfile(value: unknown): ProfileOutput | null {
  if (!isRecord(value) || !Array.isArray(value.operators)) return null;
  return {
    latency_us: typeof value.latency_us === "number" ? value.latency_us : null,
    operators: value.operators.filter(isRecord),
    truncated: value.truncated === true,
  };
}

function renderProfile(container: HTMLElement, profile: ProfileOutput | null | undefined): void {
  const summary = document.createElement("p");
  summary.textContent = profile?.latency_us != null
    ? `执行耗时：${profile.latency_us / 1000} ms` : "未返回执行耗时。";
  container.append(summary);
  if (!profile?.operators.length) {
    const empty = document.createElement("p");
    empty.textContent = "数据库未返回执行计划。";
    container.append(empty);
    return;
  }
  const table = document.createElement("div");
  renderTable(table, {
    columns: ["算子", "详情", "执行(ms)", "总计(ms)", "行数", "执行次数"],
    rows: profile.operators.map((operator) => ({
      "算子": `${"  ".repeat(Math.min(Number(operator.depth) || 0, 40))}${operator.name ?? ""}`,
      "详情": operator.details, "执行(ms)": operator.exec_time_ms,
      "总计(ms)": operator.total_time_ms, "行数": operator.rows, "执行次数": operator.executions,
    })),
    returned_row_count: profile.operators.length, result_row_count: null, truncated: profile.truncated,
  });
  table.className = "profile-table";
  container.append(table);
  if (profile.truncated) {
    const notice = document.createElement("p");
    notice.textContent = "执行计划已截断";
    container.append(notice);
  }

}

function renderFallback(root: HTMLElement, value: unknown): void {
  const fallback = document.createElement("pre");
  fallback.dataset.testid = "bounded-fallback";
  fallback.textContent = stringifyBounded(value, 4_000);
  root.replaceChildren(fallback);
}

function stringifyBounded(value: unknown, limit: number): string {
  try {
    return JSON.stringify(value, null, 2).slice(0, limit);
  } catch {
    return String(value).slice(0, limit);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const root = document.querySelector<HTMLElement>("#app");
if (root !== null) {
  mountApp(root, createBridge());
}
