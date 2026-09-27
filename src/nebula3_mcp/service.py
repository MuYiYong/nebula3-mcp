"""Application workflows exposed by the nebula3-mcp tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import uuid4

from nebula3_mcp.analysis import analyze_rows
from nebula3_mcp.config import SPACE_NAME, Settings
from nebula3_mcp.database import SUCCEEDED, ResultLike, quote_name
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.models import (
    ConnectionOutput,
    ExplanationContext,
    ListSpacesInput,
    MutationInput,
    MutationOutput,
    ParsedResult,
    QueryInput,
    QueryMetadata,
    QueryOutput,
    QueryStatus,
    ResultLimits,
    SchemaIndex,
    SchemaProperty,
    SchemaType,
    SpaceListOutput,
    SpaceSchemaInput,
    SpaceSchemaOutput,
    SpaceSelectionOutput,
    SpaceSummary,
    ValidateNGQLInput,
    ValidationEvidence,
    ValidationOutput,
)
from nebula3_mcp.policy import (
    PLAN_PREFIXES,
    evaluate_policy,
    scan_ngql,
    sentence_kind,
    validate_candidate,
)
from nebula3_mcp.profiling import extract_profile
from nebula3_mcp.result_parser import parse_result, status_of
from nebula3_mcp.serialization import JsonValue
from nebula3_mcp.specs import build_cytoscape_graph, build_vega_lite_specs

# graphd reports a missing session space as a semantic error and an unknown space in
# `USE` as an execution error; both are resolved by asking the user to choose a space.
_MISSING_SPACE_CODES = {-1009, -1005, -5}
_MISSING_SPACE_MARKERS = ("Space was not chosen", "SpaceNotFound", "Space not found")
_NAME_TOKEN = re.compile(r"`(?:[^`\\\n]|\\.)*`|[^\s;`#/]+")
_PLAN_PREFIX = re.compile(r'^\s*(?:EXPLAIN|PROFILE)\b(?:\s+FORMAT\s*=\s*"[^"\n]*")?', re.IGNORECASE)


class Gateway(Protocol):
    async def execute(self, statement: str) -> ResultLike: ...

    async def version(self) -> str: ...


@dataclass(frozen=True)
class UseClause:
    """A leading ``USE <space>;`` sentence located in the original statement text."""

    space: str
    name_start: int
    name_end: int
    body_start: int


def leading_use(statement: str) -> UseClause | None:
    scan = scan_ngql(statement)
    if not scan.sentences:
        return None
    first = scan.sentences[0]
    if sentence_kind(first.tokens) != "session":
        return None
    keyword = re.match(r"\s*USE\b", scan.sanitized[first.start:first.end], re.IGNORECASE)
    if keyword is None:
        return None
    position = _skip_trivia(statement, first.start + keyword.end())
    name = _NAME_TOKEN.match(statement, position)
    if name is None or name.end() > first.end:
        return None
    raw = name.group(0)
    space = raw[1:-1] if raw.startswith("`") else raw
    body_start = min(first.end + 1, len(statement))
    return UseClause(space, name.start(), name.end(), body_start)


def _skip_trivia(statement: str, position: int) -> int:
    """Skip whitespace and nGQL comments (``#``, ``//``, ``/* */``) in the original text."""
    while position < len(statement):
        if statement[position].isspace():
            position += 1
        elif statement.startswith("/*", position):
            end = statement.find("*/", position + 2)
            position = len(statement) if end < 0 else end + 2
        elif statement.startswith(("#", "//"), position):
            end = statement.find("\n", position)
            position = len(statement) if end < 0 else end + 1
        else:
            break
    return position


def is_missing_space(result: ResultLike) -> bool:
    return result.error_code in _MISSING_SPACE_CODES and any(
        marker in result.error_message for marker in _MISSING_SPACE_MARKERS
    )


class NebulaService:
    """Coordinates policy, database access, parsing, and portable artifacts."""

    def __init__(self, settings: Settings, gateway: Gateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.environment: str | None = None
        self.connection_id = uuid4().hex
        self.current_space: str | None = settings.default_space
        self.pending_query: QueryInput | None = None

    async def _run(self, statement: str) -> ResultLike:
        """Execute in the retained session and track the session space graphd reports."""
        result = await self.gateway.execute(statement)
        if result.space_name:
            self.current_space = result.space_name
        return result

    async def _use(self, space: str) -> ResultLike:
        return await self._run(f"USE {quote_name(space)}")

    async def select_space(self, space: str) -> SpaceSelectionOutput:
        """Select the session space and resume the last query blocked by a missing space."""
        _require_space(space)
        check = await self._use(space)
        if check.error_code != SUCCEEDED:
            raise NebulaMCPError(
                category="database_error",
                code="SPACE_SELECTION_REQUIRED",
                database_code=str(check.error_code),
                message="无法选择这个图空间，请检查名称和访问权限后重新选择。",
                suggestion="Call nebula_list_spaces, then nebula_select_space with the user's choice",
            )
        self.current_space = space
        request = self.pending_query
        result = None
        if request is not None:
            statement = request.statement
            use = leading_use(statement)
            if use is not None:
                statement = (
                    statement[:use.name_start] + quote_name(space) + statement[use.name_end:]
                )
            request = request.model_copy(update={"statement": statement, "space": None})
            result = await self.execute_query(request)
        return SpaceSelectionOutput(
            space=space, session_statement=f"USE {quote_name(space)}", result=result
        )

    async def test_connection(self) -> ConnectionOutput:
        return ConnectionOutput(
            connected=True,
            version=await self.gateway.version(),
            config=self.settings.public_view(),
            current_space=self.current_space,
        )

    async def list_spaces(self, request: ListSpacesInput) -> SpaceListOutput:
        result = await self._run("SHOW SPACES")
        parsed = parse_result(result, space=None, limits=self._limits(rows=10_000))
        names = [
            name for row in parsed.table.rows
            if isinstance(name := row.get("Name"), str) and name
        ]
        page = names[request.offset:request.offset + request.limit]
        return SpaceListOutput(
            status=parsed.status,
            spaces=[SpaceSummary(name=name, current=name == self.current_space) for name in page],
            limit=request.limit,
            offset=request.offset,
            returned_count=len(page),
            total_count=len(names),
            current_space=self.current_space,
        )

    async def get_space_schema(self, request: SpaceSchemaInput) -> SpaceSchemaOutput:
        prior = self.current_space
        used = await self._use(request.space)
        if used.error_code != SUCCEEDED:
            return SpaceSchemaOutput(
                status=status_of(used), space=request.space, tags=[], edges=[],
                session_space=self.current_space,
            )
        try:
            output = await self._read_space_schema(request)
        finally:
            if prior is not None and prior != request.space:
                await self._use(prior)
        return output.model_copy(update={"session_space": self.current_space})

    async def _read_space_schema(self, request: SpaceSchemaInput) -> SpaceSchemaOutput:
        described = await self._rows(f"DESCRIBE SPACE {quote_name(request.space)}")
        info = described[0] if described else {}
        budget = [request.max_ddl_bytes]
        truncated = [False]

        async def types(kind: Literal["tag", "edge"]) -> tuple[QueryStatus, list[SchemaType]]:
            keyword = "TAG" if kind == "tag" else "EDGE"
            listed = await self._run(f"SHOW {keyword}S")
            parsed = parse_result(listed, space=None, limits=self._limits(rows=10_000))
            items: list[SchemaType] = []
            for row in parsed.table.rows:
                name = row.get("Name")
                if not isinstance(name, str):
                    continue
                properties = [
                    SchemaProperty(
                        name=str(field.get("Field")),
                        type=_optional_string(field.get("Type")),
                        nullable=_nullable(field.get("Null")),
                        default=field.get("Default"),
                        comment=_optional_string(field.get("Comment")),
                    )
                    for field in await self._rows(f"DESCRIBE {keyword} {quote_name(name)}")
                ]
                ddl = None
                if request.include_ddl and budget[0] > 0:
                    created = await self._rows(f"SHOW CREATE {keyword} {quote_name(name)}")
                    text = created[0].get(f"Create {keyword.capitalize()}") if created else None
                    if isinstance(text, str):
                        ddl, cut = _truncate_utf8(text, budget[0])
                        budget[0] -= len(ddl.encode("utf-8"))
                        truncated[0] = truncated[0] or cut
                elif request.include_ddl:
                    truncated[0] = True
                items.append(SchemaType(kind=kind, name=name, properties=properties, ddl=ddl))
            return parsed.status, items

        tag_status, tags = await types("tag")
        _, edges = await types("edge")
        indexes: list[SchemaIndex] = []
        if request.include_indexes:
            index_sources: tuple[tuple[Literal["tag", "edge"], str, str], ...] = (
                ("tag", "TAG", "By Tag"), ("edge", "EDGE", "By Edge"),
            )
            for kind, keyword, owner in index_sources:
                for row in await self._rows(f"SHOW {keyword} INDEXES"):
                    name = row.get("Index Name")
                    if isinstance(name, str):
                        columns = row.get("Columns")
                        indexes.append(SchemaIndex(
                            kind=kind, name=name,
                            schema_name=_optional_string(row.get(owner)),
                            columns=[c for c in columns if isinstance(c, str)]
                            if isinstance(columns, list) else [],
                        ))
        return SpaceSchemaOutput(
            status=tag_status,
            space=request.space,
            vid_type=_optional_string(info.get("Vid Type")),
            partition_num=_optional_int(info.get("Partition Number")),
            replica_factor=_optional_int(info.get("Replica Factor")),
            comment=_optional_string(info.get("Comment")),
            tags=tags,
            edges=edges,
            indexes=indexes,
            ddl_truncated=truncated[0],
            session_space=self.current_space,
        )

    async def _rows(self, statement: str) -> list[dict[str, JsonValue]]:
        result = await self._run(statement)
        if result.error_code != SUCCEEDED:
            return []
        return parse_result(result, space=None, limits=self._limits(rows=10_000)).table.rows

    async def validate_ngql(self, request: ValidateNGQLInput) -> ValidationOutput:
        evidence = validate_candidate(request.statement)
        policy = evaluate_policy(request.statement, allow_mutations=False)
        if not request.run_explain:
            return ValidationOutput(evidence=evidence, policy=policy)
        if not evidence.valid or not policy.allowed or not policy.read_only:
            raise NebulaMCPError(
                category="validation_error",
                message="nGQL must pass static read-only validation before EXPLAIN",
                suggestion="Resolve validation issues and retry",
            )
        display = self._apply_space(request.statement, request.space)
        use = leading_use(display)
        body = display[use.body_start:] if use is not None else display
        if len(scan_ngql(body).sentences) != 1:
            raise NebulaMCPError(
                category="validation_error",
                message="EXPLAIN checks exactly one nGQL sentence after an optional USE",
                suggestion="Validate composite statements without run_explain",
            )
        prefix = _PLAN_PREFIX.match(body)
        sentence = body[prefix.end():] if prefix is not None else body
        prior = self.current_space
        if use is not None:
            used = await self._use(use.space)
            if used.error_code != SUCCEEDED:
                return ValidationOutput(
                    evidence=evidence, policy=policy, explain_status=status_of(used)
                )
        try:
            explained = await self._run(f"EXPLAIN {sentence.strip()}")
        finally:
            if use is not None and prior is not None and prior != use.space:
                await self._use(prior)
        status = status_of(explained)
        return ValidationOutput(
            evidence=evidence.model_copy(update={"explain_checked": status.ok}),
            policy=policy,
            explain=extract_profile(explained, max_bytes=self.settings.max_bytes)
            if status.ok else None,
            explain_status=status,
        )

    async def execute_query(self, request: QueryInput) -> QueryOutput:
        evidence = validate_candidate(request.statement)
        if not evidence.valid:
            raise NebulaMCPError(
                category="validation_error",
                message="nGQL contains unresolved dialect or placeholder issues",
                suggestion="Use the ngql-skills skill to produce NebulaGraph 3.8 nGQL",
            )
        policy = evaluate_policy(request.statement, allow_mutations=False)
        if not policy.allowed or not policy.read_only:
            raise NebulaMCPError(
                category="policy_denied",
                message="Only an approved read-only nGQL statement can use this tool",
                suggestion="Use the mutation tool only after explicitly enabling mutations",
            )
        display_statement = self._apply_space(request.statement, request.space)
        use = leading_use(display_statement)
        body = (display_statement[use.body_start:] if use is not None else display_statement)
        sentences = scan_ngql(body).sentences
        session_statement: str | None = None
        if len(sentences) == 1:
            # PROFILE wraps exactly one sentence, so a leading USE is sent on its own first.
            has_plan_prefix = sentences[0].tokens[0].upper() in PLAN_PREFIXES
            executed_statement = body.strip() if has_plan_prefix else f"PROFILE {body.strip()}"
            if use is not None:
                session_statement = f"USE {quote_name(use.space)}"
        else:
            executed_statement = display_statement
        row_limit = min(request.max_rows or self.settings.max_rows, self.settings.max_rows)
        raw_result = await self._run(session_statement) if session_statement else None
        if raw_result is None or raw_result.error_code == SUCCEEDED:
            raw_result = await self._run(executed_statement)
        if is_missing_space(raw_result):
            self.pending_query = request
            raise NebulaMCPError(
                category="database_error",
                code="SPACE_SELECTION_REQUIRED",
                database_code=str(raw_result.error_code),
                message="未选择图空间或图空间不存在，请先选择一个图空间；选好后会继续执行刚才的查询。",
                suggestion="Call nebula_list_spaces; ask the user for a space, then call "
                "nebula_select_space. The pending query is retained; do not submit it twice.",
            )
        self.pending_query = None
        effective_space = raw_result.space_name or (
            use.space if use is not None else self.current_space
        )
        parsed = parse_result(raw_result, space=effective_space, limits=self._limits(rows=row_limit))
        executed_evidence = evidence.model_copy(update={"executed": True})
        analysis = (
            analyze_rows(parsed.table.rows, parsed.truncation.truncated)
            if request.include_analysis or request.include_charts
            else None
        )
        graph_spec = build_cytoscape_graph(parsed) if request.include_graph else None
        charts = (
            build_vega_lite_specs(parsed.table.rows, analysis)
            if request.include_charts and analysis is not None
            else []
        )
        output_analysis = analysis if request.include_analysis else None
        return QueryOutput(
            status=parsed.status,
            query=QueryMetadata(
                statement=request.statement,
                executed_statement=executed_statement,
                display_statement=display_statement,
                session_statement=session_statement,
                environment=self.environment,
                connection_id=self.connection_id,
                space=effective_space,
                validation=executed_evidence,
            ),
            profile=extract_profile(raw_result, max_bytes=self.settings.max_bytes),
            table=parsed.table,
            graph=graph_spec,
            analysis=output_analysis,
            charts=charts,
            explanation_context=self._explanation_context(parsed, executed_evidence),
            truncation=parsed.truncation,
        )

    async def execute_mutation(self, request: MutationInput) -> MutationOutput:
        if not self.settings.allow_mutations:
            raise NebulaMCPError(
                category="policy_denied",
                message="Mutation execution is disabled",
                suggestion="Set NEBULA_ALLOW_MUTATIONS=true only for an appropriate database account",
            )
        if not request.confirm_mutation:
            raise NebulaMCPError(
                category="policy_denied",
                message="Explicit mutation confirmation is required",
                suggestion="Review the statement and set confirm_mutation=true",
            )
        evidence = validate_candidate(request.statement)
        policy = evaluate_policy(request.statement, allow_mutations=True)
        if not evidence.valid or not policy.allowed or policy.statement_kind != "mutation":
            raise NebulaMCPError(
                category="policy_denied",
                message="Statement is not an approved single mutation",
                suggestion="Remove placeholders, dialect residuals, and extra statements; "
                "only one USE sentence may precede the mutation",
            )
        statement = self._apply_space(request.statement, request.space)
        result = await self._run(statement)
        return MutationOutput(
            status=status_of(result),
            executed_statement=statement,
            warnings=("This tool executed a database mutation.",),
        )

    def _limits(self, *, rows: int) -> ResultLimits:
        return ResultLimits(
            rows=rows,
            nodes=self.settings.max_nodes,
            edges=self.settings.max_edges,
            bytes=self.settings.max_bytes,
        )

    @staticmethod
    def _apply_space(statement: str, space: str | None) -> str:
        if space is None:
            return statement
        _require_space(space)
        if leading_use(statement) is not None:
            raise NebulaMCPError(
                category="validation_error",
                message="space input conflicts with an explicit USE sentence",
                suggestion="Specify the space in only one place",
            )
        return f"USE {quote_name(space)};\n{statement}"

    @staticmethod
    def _explanation_context(
        parsed: ParsedResult, validation: ValidationEvidence
    ) -> ExplanationContext:
        facts = [
            f"Returned {parsed.table.returned_row_count} row(s).",
            f"Extracted {len(parsed.graph.nodes)} vertex/vertices and {len(parsed.graph.edges)} edge(s).",
        ]
        placeholders = sum(1 for node in parsed.graph.nodes if node.placeholder)
        if placeholders:
            facts.append(
                f"{placeholders} vertex/vertices appear only as edge endpoints (VID without tags)."
            )
        caveats = [f"Result was truncated by {reason}." for reason in parsed.truncation.reasons]
        caveats.extend(issue.message for issue in validation.warnings)
        caveats.append("Facts describe only returned rows; LIMIT and filters do not establish whole-graph coverage, ranking or causality.")
        return ExplanationContext(
            facts=facts,
            caveats=caveats,
            empty_result=parsed.table.returned_row_count == 0,
            validation_evidence=validation,
            suggested_focus=[
                "Explain what the returned vertices, edge types, edge direction (src -> dst) and measured values mean for the user's question.",
                "Support observations with concrete VIDs, tag properties, ranks, values and comparisons from table/graph/analysis; counts alone are insufficient.",
                "Separate observed patterns from hypotheses; identify a useful next verification when warranted.",
                "State sampling, LIMIT, missing fields and truncation limits; never infer global centrality or causality from a small sample.",
                *list(parsed.table.columns[:5]),
            ],
        )


def _require_space(value: str) -> None:
    if SPACE_NAME.fullmatch(value) is None:
        raise NebulaMCPError(
            category="validation_error",
            message="Invalid space name",
            suggestion="Use the exact name returned by nebula_list_spaces",
        )


def _optional_string(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: JsonValue | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nullable(value: JsonValue | None) -> bool | None:
    if isinstance(value, str):
        return value.upper() == "YES"
    return None


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
