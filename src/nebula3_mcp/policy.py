"""Conservative lexical checks for generated and executable nGQL (NebulaGraph 3.8)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from nebula3_mcp.models import PolicyDecision, PolicyIssue, StatementKind, ValidationEvidence

# Every write or administrative keyword of the 3.8 lexer (reserved and unreserved).
MUTATION_KEYWORDS = frozenset(
    {
        "ADD", "ALTER", "BALANCE", "CHANGE", "CLEAR", "COMPACT", "CREATE", "DELETE",
        "DIVIDE", "DOWNLOAD", "DROP", "FLUSH", "GRANT", "INGEST", "INSERT", "KILL",
        "MERGE", "REBUILD", "RECOVER", "REMOVE", "RENAME", "REVOKE", "SET", "SIGN",
        "STOP", "SUBMIT", "UPDATE", "UPSERT",
    }
)
READ_QUERY_STARTS = frozenset(
    {
        "FETCH", "FIND", "GET", "GO", "GROUP", "LIMIT", "LOOKUP", "MATCH", "OPTIONAL",
        "ORDER", "RETURN", "UNWIND", "WITH", "YIELD",
    }
)
CATALOG_STARTS = frozenset({"DESC", "DESCRIBE", "SHOW"})
PLAN_PREFIXES = frozenset({"EXPLAIN", "PROFILE"})
AGGREGATE_PATTERN = re.compile(r"\b(?:AVG|COLLECT|COLLECT_SET|COUNT|MAX|MIN|SUM|STD|BIT_AND|BIT_OR|BIT_XOR)\s*\(", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]*|;|=")
PLACEHOLDER_PATTERN = re.compile(r"<[A-Za-z_][^>]*>|\{\{[^{}]+\}\}")


DIALECT_RULES: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "gql_session_set",
        "gql",
        re.compile(r"\bSESSION\s+SET\b", re.IGNORECASE),
        "YueShu/ISO GQL SESSION SET is not nGQL; select a space with USE <space>.",
    ),
    (
        "gql_graph_type",
        "gql",
        re.compile(r"\bGRAPH\s+TYPE\b", re.IGNORECASE),
        "Graph Type belongs to YueShu 5.x GQL; NebulaGraph 3.8 uses TAG and EDGE types.",
    ),
    (
        "gql_catalog_path",
        "gql",
        re.compile(r"\bUSE\s+/", re.IGNORECASE),
        "Catalog paths such as /schema/graph are not nGQL; use USE <space>.",
    ),
    (
        "gql_insert_pattern",
        "gql",
        re.compile(r"\bINSERT\s*\(", re.IGNORECASE),
        "GQL INSERT (...) patterns are not nGQL; use INSERT VERTEX/EDGE ... VALUES.",
    ),
    (
        "neo4j_procedure",
        "cypher",
        re.compile(r"\bCALL\s+(?:apoc|db|dbms|gds)\.", re.IGNORECASE),
        "Neo4j-specific procedures cannot be executed by NebulaGraph.",
    ),
    (
        "call_procedure",
        "gql",
        re.compile(r"\bCALL\b", re.IGNORECASE),
        "NebulaGraph 3.8 has no CALL procedures or subqueries.",
    ),
    (
        "cypher_subquery",
        "cypher",
        re.compile(r"\b(?:EXISTS|COUNT|COLLECT)\s*\{", re.IGNORECASE),
        "Cypher subquery expressions are not supported by NebulaGraph 3.8.",
    ),
    (
        "cypher_merge",
        "cypher",
        re.compile(r"\bMERGE\s*\(", re.IGNORECASE),
        "Cypher MERGE is not supported; use nGQL INSERT/UPSERT.",
    ),
    (
        "cypher_create_pattern",
        "cypher",
        re.compile(r"\bCREATE\s*\(", re.IGNORECASE),
        "Cypher CREATE (...) is not supported; use INSERT VERTEX/EDGE ... VALUES.",
    ),
    (
        "cypher_match_set",
        "cypher",
        re.compile(r"\bMATCH\b.*\b(?:SET|REMOVE)\b", re.IGNORECASE | re.DOTALL),
        "Cypher MATCH ... SET/REMOVE is not supported; use nGQL UPDATE.",
    ),
    (
        "cypher_detach_delete",
        "cypher",
        re.compile(r"\bDETACH\s+DELETE\b", re.IGNORECASE),
        "Cypher DETACH DELETE is not supported; use DELETE VERTEX ... WITH EDGE.",
    ),
    (
        "cypher_foreach",
        "cypher",
        re.compile(r"\bFOREACH\b", re.IGNORECASE),
        "Cypher FOREACH is not supported by NebulaGraph 3.8.",
    ),
    (
        "cypher_element_id",
        "cypher",
        re.compile(r"\belementId\s*\(", re.IGNORECASE),
        "Cypher elementId() is not available; use id(v) for the vertex VID.",
    ),
)


@dataclass(frozen=True)
class Sentence:
    """One ``;``-separated sentence, with its sanitized offsets in the full statement."""

    start: int
    end: int
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class ScannedNGQL:
    """Lexical view with quoted/comment content removed and offsets preserved."""

    sanitized: str
    tokens: tuple[str, ...]
    sentences: tuple[Sentence, ...]


def _blank_quoted_and_commented(statement: str) -> str:
    """Blank strings, backtick labels and comments exactly where graphd's lexer skips them."""
    chars = list(statement)
    index = 0
    length = len(chars)
    while index < length:
        char = chars[index]
        following = chars[index + 1] if index + 1 < length else ""
        if char == "/" and following == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < length:
                if chars[index] == "*" and index + 1 < length and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] not in "\r\n":
                    chars[index] = " "
                index += 1
            continue
        # "--" is the undirected edge token in nGQL, not a comment.
        if char == "#" or (char == "/" and following == "/"):
            while index < length and chars[index] not in "\r\n":
                chars[index] = " "
                index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            chars[index] = " "
            index += 1
            while index < length:
                current = chars[index]
                if current == "\\" and index + 1 < length:
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    continue
                chars[index] = " "
                index += 1
                if current == quote:
                    break
            continue
        index += 1
    return "".join(chars)


def scan_ngql(statement: str) -> ScannedNGQL:
    sanitized = _blank_quoted_and_commented(statement)
    sentences: list[Sentence] = []
    tokens: list[str] = []
    start = 0
    current: list[str] = []
    for match in TOKEN_PATTERN.finditer(sanitized):
        token = match.group(0)
        if token == ";":
            if current:
                sentences.append(Sentence(start, match.start(), tuple(current)))
            current, start = [], match.end()
            continue
        tokens.append(token)
        current.append(token)
    if current:
        sentences.append(Sentence(start, len(sanitized), tuple(current)))
    return ScannedNGQL(sanitized=sanitized, tokens=tuple(tokens), sentences=tuple(sentences))


def _sentence_body(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Drop EXPLAIN/PROFILE [FORMAT=...] and a leading ``$var =`` assignment."""
    upper = [token.upper() for token in tokens]
    index = 0
    while index < len(upper) and upper[index] in PLAN_PREFIXES:
        index += 1
        if index < len(upper) and upper[index] == "FORMAT":
            index += 1
            if index < len(upper) and upper[index] == "=":
                index += 1
    if index + 1 < len(upper) and upper[index].startswith("$") and upper[index + 1] == "=":
        index += 2
    return tuple(token for token in upper[index:] if token != "=")


SentenceKind = Literal["session", "catalog", "query", "mutation", "unknown"]


def sentence_kind(tokens: tuple[str, ...]) -> SentenceKind:
    body = _sentence_body(tokens)
    if not body:
        return "unknown"
    words = [token for token in body if not token.startswith("$")]
    if not words:
        return "unknown"
    if words[0] == "USE":
        return "session" if len(words) <= 2 else "unknown"
    if words[0] == "SHOW" and len(words) > 1 and words[1] == "CREATE":
        words = [words[0], *words[2:]]  # SHOW CREATE TAG/EDGE/SPACE only reads DDL.
    if any(word in MUTATION_KEYWORDS for word in words):
        return "mutation"
    if words[0] in CATALOG_STARTS:
        return "catalog"
    if words[0] in READ_QUERY_STARTS:
        return "query"
    return "unknown"


def _statement_kind(scan: ScannedNGQL) -> StatementKind:
    kinds = [sentence_kind(sentence.tokens) for sentence in scan.sentences]
    if not kinds:
        return "unknown"
    if "mutation" in kinds:
        return "mutation"
    if "unknown" in kinds:
        return "unknown"
    effective = [kind for kind in kinds if kind != "session"]
    if not effective:
        return "session"
    if all(kind == "catalog" for kind in effective):
        return "catalog"
    return "query"


def _is_single_mutation(scan: ScannedNGQL) -> bool:
    """A mutation may be preceded by at most one ``USE <space>`` sentence."""
    kinds = [sentence_kind(sentence.tokens) for sentence in scan.sentences]
    if kinds and kinds[0] == "session":
        kinds = kinds[1:]
    return kinds == ["mutation"]


def evaluate_policy(statement: str, allow_mutations: bool) -> PolicyDecision:
    scan = scan_ngql(statement)
    kind = _statement_kind(scan)
    reasons: list[str] = []

    if not statement.strip():
        reasons.append("empty_statement")
    if kind == "unknown":
        reasons.append("unknown_statement_kind")
    if kind == "mutation":
        if not allow_mutations:
            reasons.append("mutations_disabled")
        if not _is_single_mutation(scan):
            reasons.append("multiple_statements")

    read_only = kind in {"catalog", "query", "session"} and not reasons
    allowed = not reasons and (read_only or (kind == "mutation" and allow_mutations))
    return PolicyDecision(
        statement_kind=kind,
        allowed=allowed,
        read_only=read_only,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def validate_candidate(statement: str) -> ValidationEvidence:
    scan = scan_ngql(statement)
    issues: list[PolicyIssue] = []
    warnings: list[PolicyIssue] = []
    dialects: set[str] = set()

    placeholders = tuple(match.group(0) for match in PLACEHOLDER_PATTERN.finditer(statement))
    if placeholders:
        issues.append(PolicyIssue(code="placeholder", message="Replace all schema placeholders."))

    for code, dialect, pattern, message in DIALECT_RULES:
        if pattern.search(scan.sanitized):
            dialects.add(dialect)
            issues.append(PolicyIssue(code=code, message=message))

    decision = evaluate_policy(statement, allow_mutations=False)
    if "empty_statement" in decision.reasons:
        issues.append(PolicyIssue(code="empty_statement", message="Empty statement"))

    if (
        re.search(r"\b(?:MATCH|LOOKUP)\b", scan.sanitized, re.IGNORECASE)
        and re.search(r"\bLIMIT\b", scan.sanitized, re.IGNORECASE) is None
        and AGGREGATE_PATTERN.search(scan.sanitized) is None
    ):
        warnings.append(
            PolicyIssue(
                code="missing_limit",
                message="Non-aggregate MATCH/LOOKUP has no LIMIT; result and scan cost may be large.",
            )
        )

    detected_dialect: Literal["ngql", "gql", "cypher", "unknown"]
    if "cypher" in dialects:
        detected_dialect = "cypher"
    elif "gql" in dialects:
        detected_dialect = "gql"
    elif statement.strip():
        detected_dialect = "ngql"
    else:
        detected_dialect = "unknown"

    return ValidationEvidence(
        valid=not issues,
        detected_dialect=detected_dialect,
        statement_kind=decision.statement_kind,
        issues=tuple(issues),
        warnings=tuple(warnings),
        placeholders=placeholders,
    )
