export interface GraphElement {
  data: Record<string, unknown> & { id: string };
}

export interface GraphElements {
  nodes: GraphElement[];
  edges: GraphElement[];
}

export interface GraphSpec {
  format: "cytoscape-elements-v1";
  space: string | null;
  elements: GraphElements;
  paths: Record<string, unknown>[];
  truncated: boolean;
}

export interface TableResult {
  columns: string[];
  rows: Record<string, unknown>[];
  returned_row_count: number;
  result_row_count: number | null;
  truncated: boolean;
}

export interface ChartSpec {
  format: "vega-lite-v5";
  description: string;
  spec: Record<string, unknown>;
}

export interface ProfileOutput {
  latency_us: number | null;
  operators: Record<string, unknown>[];
  truncated: boolean;
}

export interface QueryOutput {
  profile?: ProfileOutput | null;
  query: {
    statement: string;
    executed_statement: string;
    display_statement?: string;
    session_statement?: string;
    connection_id?: string;
    environment?: string;
    space: string | null;
    read_only: boolean;
  };
  table: TableResult;
  graph: GraphSpec | null;
  analysis: Record<string, unknown> | null;
  charts: ChartSpec[];
  explanation_context: Record<string, unknown>;
  truncation: { truncated: boolean; reasons: string[] };
}

export interface QueryPresentation {
  result: QueryOutput;
  explanation: string;
}
