from __future__ import annotations

import pytest

from nebula3_mcp.policy import evaluate_policy, scan_ngql, validate_candidate


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (v:player) RETURN v LIMIT 10",
        "OPTIONAL MATCH (v) RETURN v LIMIT 1",
        'GO FROM "player100" OVER follow YIELD dst(edge) AS id | LIMIT 3',
        'FETCH PROP ON player "player100" YIELD properties(vertex)',
        "LOOKUP ON player WHERE player.age > 40 YIELD id(vertex) | LIMIT 5",
        'FIND SHORTEST PATH FROM "a" TO "b" OVER * YIELD path AS p',
        'GET SUBGRAPH 1 STEPS FROM "a" YIELD VERTICES AS nodes, EDGES AS relationships',
        "UNWIND [1, 2] AS x RETURN x",
        "WITH 1 AS x RETURN x",
        "YIELD 1 AS x",
        "RETURN 1",
        "EXPLAIN MATCH (v) RETURN v LIMIT 1",
        'PROFILE FORMAT="row" MATCH (v) RETURN v LIMIT 1',
        "USE basketballplayer; MATCH (v) RETURN v LIMIT 1",
        '$a = GO FROM "p" OVER follow YIELD dst(edge) AS id; GO FROM $a.id OVER follow YIELD dst(edge)',
        "MATCH (a)--(b) RETURN b LIMIT 1",
        "MATCH (v) RETURN v.player.name AS `delete` LIMIT 1",
        "MATCH (v) WHERE v.player.name == 'DROP SPACE x' RETURN v LIMIT 1",
        "MATCH (v) RETURN collect_set(v) AS s",
    ],
)
def test_read_only_ngql_is_allowed(statement: str) -> None:
    decision = evaluate_policy(statement, allow_mutations=False)

    assert decision.allowed is True, decision
    assert decision.read_only is True


@pytest.mark.parametrize(
    "statement",
    ["SHOW SPACES", "SHOW TAGS", "DESCRIBE TAG player", "DESC EDGE follow",
     "SHOW CREATE TAG player", "SHOW CREATE SPACE basketballplayer", "SHOW TAG INDEXES"],
)
def test_catalog_statements_are_read_only(statement: str) -> None:
    decision = evaluate_policy(statement, allow_mutations=False)

    assert decision.statement_kind == "catalog"
    assert decision.allowed is True


@pytest.mark.parametrize(
    "statement",
    [
        'INSERT VERTEX player(name, age) VALUES "p1":("A", 1)',
        'UPDATE VERTEX ON player "p1" SET age = 2',
        'UPSERT VERTEX ON player "p1" SET age = 2',
        'DELETE VERTEX "p1" WITH EDGE',
        "CREATE TAG t(name string)",
        "DROP SPACE demo",
        "ALTER TAG player ADD (x int)",
        "REBUILD TAG INDEX player_index_0",
        "SUBMIT JOB STATS",
        "STOP JOB 1",
        "BALANCE DATA",
        "KILL QUERY (session=1, plan=2)",
        "CLEAR SPACE demo",
        "GRANT ROLE ADMIN ON demo TO user",
        "CHANGE PASSWORD root FROM 'a' TO 'b'",
        'GO FROM "p" OVER follow YIELD src(edge) AS s, dst(edge) AS d | DELETE EDGE follow $-.s -> $-.d',
        "MATCH (a)--(b) RETURN b; DROP SPACE demo",
        "SHOW CREATE TAG x | DROP TAG x",
        "EXPLAIN INSERT VERTEX t() VALUES \"a\":()",
    ],
)
def test_mutations_are_denied_by_default(statement: str) -> None:
    decision = evaluate_policy(statement, allow_mutations=False)

    assert decision.statement_kind == "mutation", decision
    assert decision.allowed is False
    assert "mutations_disabled" in decision.reasons


def test_double_dash_is_an_edge_not_a_comment() -> None:
    # A lexer that treated "--" as a comment would hide the DELETE from the policy.
    decision = evaluate_policy("MATCH (a)--(b) DELETE VERTEX 'x'", allow_mutations=False)

    assert decision.statement_kind == "mutation"


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (v) RETURN v LIMIT 1 # it's a comment\n",
        "MATCH (v) RETURN v LIMIT 1 // DROP SPACE demo",
        "/* DELETE VERTEX 'x' */ MATCH (v) RETURN v LIMIT 1",
    ],
)
def test_comments_follow_the_graphd_lexer(statement: str) -> None:
    assert evaluate_policy(statement, allow_mutations=False).allowed is True


def test_quote_inside_hash_comment_does_not_hide_next_line() -> None:
    statement = "MATCH (v) RETURN v LIMIT 1 # it's\n; DROP SPACE demo"

    assert evaluate_policy(statement, allow_mutations=False).statement_kind == "mutation"


def test_enabled_mutation_must_be_single_after_optional_use() -> None:
    single = evaluate_policy('USE demo; INSERT VERTEX t() VALUES "a":()', allow_mutations=True)
    double = evaluate_policy(
        'INSERT VERTEX t() VALUES "a":(); INSERT VERTEX t() VALUES "b":()', allow_mutations=True
    )
    mixed = evaluate_policy('MATCH (v) RETURN v; DELETE VERTEX "a"', allow_mutations=True)

    assert single.allowed is True and single.statement_kind == "mutation"
    assert double.allowed is False and "multiple_statements" in double.reasons
    assert mixed.allowed is False


@pytest.mark.parametrize("statement", ["", "   ", "HELLO world", "CALL db.labels()", "USE a b c"])
def test_unknown_or_empty_statements_are_denied(statement: str) -> None:
    decision = evaluate_policy(statement, allow_mutations=True)

    assert decision.allowed is False


def test_session_only_use_is_read_only() -> None:
    decision = evaluate_policy("USE demo", allow_mutations=False)

    assert decision.statement_kind == "session"
    assert decision.allowed is True


def test_scanner_splits_sentences_outside_quotes() -> None:
    scan = scan_ngql("USE `a;b`; RETURN 'x;y' AS v; ")

    assert [sentence.tokens for sentence in scan.sentences] == [("USE",), ("RETURN", "AS", "v")]


@pytest.mark.parametrize(
    ("statement", "code", "dialect"),
    [
        ("SESSION SET GRAPH demo", "gql_session_set", "gql"),
        ("DESCRIBE GRAPH TYPE /s/t", "gql_graph_type", "gql"),
        ("USE /default_schema/demo MATCH (n) RETURN n LIMIT 1", "gql_catalog_path", "gql"),
        ("CALL show_graphs() RETURN *", "call_procedure", "gql"),
        ("CALL db.labels()", "neo4j_procedure", "cypher"),
        ("MATCH (n) WHERE EXISTS { (n)--() } RETURN n LIMIT 1", "cypher_subquery", "cypher"),
        ("MERGE (n:player {name: 'a'})", "cypher_merge", "cypher"),
        ("CREATE (n:player)", "cypher_create_pattern", "cypher"),
        ("MATCH (n) SET n.age = 1", "cypher_match_set", "cypher"),
        ("MATCH (n) DETACH DELETE n", "cypher_detach_delete", "cypher"),
        ("MATCH (n) RETURN elementId(n) LIMIT 1", "cypher_element_id", "cypher"),
    ],
)
def test_non_ngql_residuals_are_rejected(statement: str, code: str, dialect: str) -> None:
    evidence = validate_candidate(statement)

    assert evidence.valid is False
    assert code in {issue.code for issue in evidence.issues}
    assert evidence.detected_dialect == dialect


def test_placeholders_are_rejected_but_edge_arrows_are_not_placeholders() -> None:
    evidence = validate_candidate('MATCH (v)<-[e:follow]-(n) WHERE id(v) == "<vid>" RETURN n LIMIT 1')

    assert evidence.valid is False
    assert evidence.placeholders == ("<vid>",)
    assert validate_candidate("MATCH (v)<-[e:follow]-(n) RETURN n LIMIT 1").valid is True


def test_unbounded_match_and_lookup_warn_but_aggregates_do_not() -> None:
    assert validate_candidate("MATCH (v) RETURN v").warnings[0].code == "missing_limit"
    assert validate_candidate("LOOKUP ON player YIELD id(vertex)").warnings[0].code == "missing_limit"
    assert validate_candidate("MATCH (v) RETURN count(v)").warnings == ()
    assert validate_candidate("MATCH (v) RETURN v LIMIT 3").detected_dialect == "ngql"
