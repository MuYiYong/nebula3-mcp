"""Structured contracts shared by nebula3-mcp components."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyIssue(BaseModel):
    """One static validation finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


StatementKind = Literal["query", "catalog", "session", "mutation", "unknown"]


class PolicyDecision(BaseModel):
    """Server-side execution decision for one nGQL statement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement_kind: StatementKind
    allowed: bool
    read_only: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ValidationEvidence(BaseModel):
    """Static evidence about a generated nGQL candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    detected_dialect: Literal["ngql", "gql", "cypher", "unknown"]
    statement_kind: StatementKind
    issues: tuple[PolicyIssue, ...] = ()
    warnings: tuple[PolicyIssue, ...] = ()
    explain_checked: bool = False
    executed: bool = False
    placeholders: tuple[str, ...] = Field(default_factory=tuple)


class ResultLimits(BaseModel):
    """Hard bounds applied while consuming a query result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: int = Field(ge=1)
    nodes: int = Field(ge=1)
    edges: int = Field(ge=1)
    bytes: int = Field(ge=1)


class QueryStatus(BaseModel):
    """Database status returned alongside a parsed result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    code: str
    message: str
    latency_us: int | None = None
    space: str | None = None
    comment: str | None = None


class TableResult(BaseModel):
    """Bounded row-oriented view of the result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: list[str]
    rows: list[dict[str, Any]]
    returned_row_count: int
    result_row_count: int | None = None
    truncated: bool = False


class GraphNode(BaseModel):
    """Normalized vertex extracted from a result value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    space: str | None
    vid: Any
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    placeholder: bool = False


class GraphEdge(BaseModel):
    """Normalized edge extracted from a result value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    space: str | None
    src: Any
    dst: Any
    rank: Any = 0
    edge_type: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPath(BaseModel):
    """Path metadata with the SDK length kept separate from extracted hop count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sdk_length: int | None = None
    hop_count: int
    node_keys: list[str] = Field(default_factory=list)
    edge_keys: list[str] = Field(default_factory=list)


class ParsedGraph(BaseModel):
    """Graph entities discovered while walking returned rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    space: str | None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    paths: list[GraphPath] = Field(default_factory=list)


class TruncationInfo(BaseModel):
    """Exact server-side result limits that affected the response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    truncated: bool = False
    reasons: tuple[str, ...] = ()


class ParsedResult(BaseModel):
    """Typed, bounded representation shared by output renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QueryStatus
    table: TableResult
    graph: ParsedGraph
    truncation: TruncationInfo


class CytoscapeElement(BaseModel):
    """One Cytoscape element with an extensible data payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: dict[str, Any]


class CytoscapeElements(BaseModel):
    """Cytoscape node and edge collections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: list[CytoscapeElement] = Field(default_factory=list)
    edges: list[CytoscapeElement] = Field(default_factory=list)


class GraphSpec(BaseModel):
    """Portable graph artifact returned to an MCP client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["cytoscape-elements-v1"] = "cytoscape-elements-v1"
    space: str | None
    elements: CytoscapeElements
    paths: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class TopValue(BaseModel):
    """One deterministic categorical frequency entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Any
    count: int


ColumnKind = Literal["numeric", "temporal", "categorical", "empty", "unsupported"]


class ColumnAnalysis(BaseModel):
    """Facts calculated for one returned column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ColumnKind
    count: int
    null_count: int
    minimum: int | float | None = None
    maximum: int | float | None = None
    mean: float | None = None
    median: float | None = None
    top_values: list[TopValue] = Field(default_factory=list)


class Analysis(BaseModel):
    """Deterministic statistics scoped only to returned rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["returned_rows"] = "returned_rows"
    complete_result: bool
    row_count: int
    columns: dict[str, ColumnAnalysis]


class ChartSpec(BaseModel):
    """Portable Vega-Lite artifact returned to an MCP client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["vega-lite-v5"] = "vega-lite-v5"
    description: str
    spec: dict[str, Any]


class ListSpacesInput(BaseModel):
    """Pagination controls for space discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=100_000)


class SpaceSchemaInput(BaseModel):
    """Target space and optional bounded DDL request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    space: str = Field(min_length=1, max_length=128, pattern=r"^[^`\x00-\x1f]+$")
    include_ddl: bool = False
    include_indexes: bool = True
    max_ddl_bytes: int = Field(65_536, ge=1, le=1_048_576)


class ValidateNGQLInput(BaseModel):
    """Static validation request with optional EXPLAIN evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=1_000_000)
    space: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[^`\x00-\x1f]+$")
    run_explain: bool = False


class QueryInput(BaseModel):
    """Read-only query execution request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=1_000_000)
    space: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[^`\x00-\x1f]+$")
    max_rows: int | None = Field(None, ge=1, le=10_000)
    include_graph: bool = True
    include_analysis: bool = True
    include_charts: bool = True
    render_mode: Literal["spec"] = "spec"


class MutationInput(BaseModel):
    """Explicitly confirmed mutation execution request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=1_000_000)
    space: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[^`\x00-\x1f]+$")
    confirm_mutation: bool = False


class ConnectionOutput(BaseModel):
    """Redacted connectivity evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connected: bool
    version: str
    config: dict[str, Any]
    current_space: str | None = None


class SpaceSummary(BaseModel):
    """One space returned by SHOW SPACES."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    current: bool = False


class SpaceListOutput(BaseModel):
    """Typed page of available spaces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QueryStatus
    spaces: list[SpaceSummary]
    limit: int
    offset: int
    returned_count: int
    total_count: int
    current_space: str | None = None


class SchemaProperty(BaseModel):
    """One property row from DESCRIBE TAG/EDGE."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: str | None = None
    nullable: bool | None = None
    default: Any = None
    comment: str | None = None


class SchemaType(BaseModel):
    """One tag or edge type of a space."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tag", "edge"]
    name: str
    properties: list[SchemaProperty] = Field(default_factory=list)
    ddl: str | None = None


class SchemaIndex(BaseModel):
    """One native tag or edge index."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tag", "edge"]
    name: str
    schema_name: str | None = None
    columns: list[str] = Field(default_factory=list)


class SpaceSchemaOutput(BaseModel):
    """Structured space schema with optional bounded DDL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QueryStatus
    space: str
    vid_type: str | None = None
    partition_num: int | None = None
    replica_factor: int | None = None
    comment: str | None = None
    tags: list[SchemaType]
    edges: list[SchemaType]
    indexes: list[SchemaIndex] = Field(default_factory=list)
    ddl_truncated: bool = False
    session_space: str | None = None


class ProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latency_us: int | None = None
    format: str | None = None
    optimize_time_us: int | None = None
    operators: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class ValidationOutput(BaseModel):
    """Static policy evidence and optional non-executing EXPLAIN result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: ValidationEvidence
    policy: PolicyDecision
    explain: ProfileOutput | None = None
    explain_status: QueryStatus | None = None


class QueryMetadata(BaseModel):
    """Execution context recorded with a query result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str
    executed_statement: str
    display_statement: str | None = None
    session_statement: str | None = Field(
        None, description="A USE sentence sent separately before executed_statement."
    )
    environment: str | None = None
    connection_id: str | None = None
    space: str | None
    read_only: bool = True
    validation: ValidationEvidence


class ExplanationContext(BaseModel):
    """Auditable facts for the MCP client to explain in natural language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    facts: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    empty_result: bool
    validation_evidence: ValidationEvidence
    suggested_focus: list[str] = Field(default_factory=list)


class QueryHistoryEntry(BaseModel):
    """One user statement sent in the current database session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    statement: str = Field(description="nGQL as the user sees it; automatic PROFILE omitted.")
    kind: Literal["query", "mutation", "use"]
    space: str | None = None
    ok: bool
    code: str | None = None
    executed_at: str = Field(description="UTC ISO-8601 timestamp.")
    result_id: str | None = None


class QueryOutput(BaseModel):
    """Unified read-only query output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: str | None = Field(
        None,
        description="Pass this id to nebula_render_result instead of re-sending the result.",
    )
    status: QueryStatus
    query: QueryMetadata
    profile: ProfileOutput | None = None
    table: TableResult
    graph: GraphSpec | None = Field(
        None,
        description=(
            "Render this Cytoscape graph when enabled and its elements contain nodes or edges."
        ),
    )
    analysis: Analysis | None = Field(
        None,
        description="Deterministic facts about the bounded returned rows when enabled.",
    )
    charts: list[ChartSpec] = Field(
        default_factory=list,
        description=(
            "Render every Vega-Lite chart in this list when charts are enabled and the list "
            "is non-empty; a table is not a chart replacement."
        ),
    )
    explanation_context: ExplanationContext = Field(
        description=(
            "Use this evidence to write a human-readable explanation for every successful query."
        )
    )
    truncation: TruncationInfo


class QueryPresentation(BaseModel):
    """Any query result and client-authored explanation for the MCP App."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: QueryOutput
    explanation: str = Field(
        min_length=1, max_length=100_000,
        description="Explain result meaning and insights with concrete entities, directions, values "
        "and comparisons. Distinguish facts from hypotheses and state sampling/missing-data limits. "
        "Do not merely repeat row or path counts.",
    )
    history: list[QueryHistoryEntry] = Field(
        default_factory=list,
        description="Statements sent in the current database session, oldest first.",
    )


class SpaceSelectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    space: str
    session_statement: str
    result: QueryOutput | None = None


class MutationOutput(BaseModel):
    """Mutation status without query visualization artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QueryStatus
    executed_statement: str
    session_statement: str | None = None
    warnings: tuple[str, ...] = ()


class EnvironmentsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active: str | None
    environments: list[str]
