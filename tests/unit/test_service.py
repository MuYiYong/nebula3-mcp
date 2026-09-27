from __future__ import annotations

import pytest

from nebula3_mcp.config import Settings
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.models import (
    ListSpacesInput,
    MutationInput,
    QueryInput,
    SpaceSchemaInput,
    ValidateNGQLInput,
)
from nebula3_mcp.service import NebulaService
from tests.fakes import SPACE_NOT_FOUND, FakeGateway, FakeResult, edge, vertex
from tests.unit.test_profile import node, plan


def mutation_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"allow_mutations": True})


@pytest.mark.anyio
async def test_connection_output_is_redacted(settings: Settings) -> None:
    service = NebulaService(settings, FakeGateway())

    output = await service.test_connection()

    assert output.connected is True
    assert output.version == "3.8.0"
    assert output.config["username"] == "r***r"
    assert output.current_space is None
    assert "runtime-secret" not in output.model_dump_json()


@pytest.mark.anyio
async def test_query_requires_read_only_policy_before_gateway_call(settings: Settings) -> None:
    gateway = FakeGateway()

    with pytest.raises(NebulaMCPError) as caught:
        await NebulaService(settings, gateway).execute_query(QueryInput(statement="DROP SPACE demo"))

    assert caught.value.category == "policy_denied"
    assert gateway.statements == []


@pytest.mark.anyio
async def test_query_rejects_unconverted_gql_before_gateway_call(settings: Settings) -> None:
    gateway = FakeGateway()

    with pytest.raises(NebulaMCPError) as caught:
        await NebulaService(settings, gateway).execute_query(
            QueryInput(statement="USE /default_schema/demo MATCH (n) RETURN n LIMIT 1")
        )

    assert caught.value.category == "validation_error"
    assert gateway.statements == []


@pytest.mark.anyio
async def test_space_input_sends_use_separately_then_profiles_one_sentence(settings: Settings) -> None:
    rows = [{"sector": "A", "score": 1.0}, {"sector": "B", "score": 2.0}]
    gateway = FakeGateway([FakeResult(rows, space="demo", plan_desc=plan(node(1, "Project", None, rows=2)))])
    service = NebulaService(settings, gateway)

    output = await service.execute_query(
        QueryInput(statement="RETURN 'A' AS sector, 1.0 AS score", space="demo")
    )

    assert gateway.statements == ["USE `demo`", "PROFILE RETURN 'A' AS sector, 1.0 AS score"]
    assert output.query.statement == "RETURN 'A' AS sector, 1.0 AS score"
    assert output.query.display_statement == "USE `demo`;\nRETURN 'A' AS sector, 1.0 AS score"
    assert output.query.session_statement == "USE `demo`"
    assert output.query.executed_statement == "PROFILE RETURN 'A' AS sector, 1.0 AS score"
    assert output.query.space == "demo"
    assert service.current_space == "demo"
    assert output.profile is not None and output.profile.operators[0]["name"] == "Project"
    assert output.graph is not None and output.graph.format == "cytoscape-elements-v1"
    assert output.analysis is not None
    assert output.charts[0].format == "vega-lite-v5"
    assert output.explanation_context.validation_evidence.executed is True


@pytest.mark.anyio
async def test_explicit_use_sentence_is_split_for_profile(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult([{"v": vertex("p1", "player", name="Tim")}], space="nba")])

    output = await NebulaService(settings, gateway).execute_query(
        QueryInput(statement="USE nba; MATCH (v:player) RETURN v LIMIT 1")
    )

    assert gateway.statements == ["USE `nba`", "PROFILE MATCH (v:player) RETURN v LIMIT 1"]
    assert output.query.display_statement == "USE nba; MATCH (v:player) RETURN v LIMIT 1"
    assert output.graph is not None
    assert output.graph.space == "nba"
    assert output.graph.elements.nodes[0].data["id"] == "nba:p1"


@pytest.mark.anyio
async def test_composite_and_prefixed_statements_are_not_wrapped(settings: Settings) -> None:
    composite = '$a = GO FROM "p" OVER follow YIELD dst(edge) AS id; GO FROM $a.id OVER follow YIELD dst(edge) AS d'
    gateway = FakeGateway([FakeResult([{"d": "x"}]), FakeResult([{"n": 1}])])
    service = NebulaService(settings, gateway)

    first = await service.execute_query(QueryInput(statement=composite))
    second = await service.execute_query(QueryInput(statement="EXPLAIN MATCH (v) RETURN v LIMIT 1"))

    assert gateway.statements == [composite, "EXPLAIN MATCH (v) RETURN v LIMIT 1"]
    assert first.query.executed_statement == composite
    assert first.query.session_statement is None
    assert second.query.executed_statement == "EXPLAIN MATCH (v) RETURN v LIMIT 1"


@pytest.mark.anyio
async def test_query_result_components_remain_individually_configurable(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult([{"sector": "A", "score": 1.0}])])

    output = await NebulaService(settings, gateway).execute_query(
        QueryInput(statement="RETURN 'A' AS sector, 1.0 AS score",
                   include_graph=False, include_analysis=False, include_charts=False)
    )

    assert output.graph is None
    assert output.analysis is None
    assert output.charts == []


@pytest.mark.anyio
async def test_space_input_conflicts_with_explicit_use(settings: Settings) -> None:
    gateway = FakeGateway()

    with pytest.raises(NebulaMCPError, match="USE"):
        await NebulaService(settings, gateway).execute_query(
            QueryInput(statement="USE other; RETURN 1", space="demo")
        )

    assert gateway.statements == []


@pytest.mark.anyio
async def test_edges_without_vertices_produce_placeholder_endpoints(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult([{"e": edge("a", "b", "follow")}], space="s")])

    output = await NebulaService(settings, gateway).execute_query(
        QueryInput(statement='GO FROM "a" OVER follow YIELD edge AS e')
    )

    assert gateway.statements == ['PROFILE GO FROM "a" OVER follow YIELD edge AS e']
    assert output.graph is not None
    assert [n.data["placeholder"] for n in output.graph.elements.nodes] == [True, True]
    assert any("edge endpoints" in fact for fact in output.explanation_context.facts)
    assert len(output.explanation_context.suggested_focus) >= 4


@pytest.mark.anyio
async def test_mutation_needs_switch_and_confirmation(settings: Settings) -> None:
    gateway = FakeGateway()
    statement = 'INSERT VERTEX player(name) VALUES "p9":("N")'

    with pytest.raises(NebulaMCPError, match="disabled"):
        await NebulaService(settings, gateway).execute_mutation(
            MutationInput(statement=statement, confirm_mutation=True)
        )
    with pytest.raises(NebulaMCPError, match="confirmation"):
        await NebulaService(mutation_settings(settings), gateway).execute_mutation(
            MutationInput(statement=statement, confirm_mutation=False)
        )
    assert gateway.statements == []


@pytest.mark.anyio
async def test_enabled_confirmed_mutation_runs_once_in_the_requested_space(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult(space="demo")])
    service = NebulaService(mutation_settings(settings), gateway)

    output = await service.execute_mutation(MutationInput(
        statement='INSERT VERTEX player(name) VALUES "p9":("N")', space="demo", confirm_mutation=True,
    ))

    assert gateway.statements == ['USE `demo`;\nINSERT VERTEX player(name) VALUES "p9":("N")']
    assert output.status.ok is True
    assert output.warnings == ("This tool executed a database mutation.",)


@pytest.mark.anyio
async def test_read_query_cannot_be_smuggled_through_mutation_tool(settings: Settings) -> None:
    gateway = FakeGateway()
    service = NebulaService(mutation_settings(settings), gateway)

    for statement in ("MATCH (v) RETURN v LIMIT 1",
                      'INSERT VERTEX t() VALUES "a":(); DROP SPACE demo'):
        with pytest.raises(NebulaMCPError):
            await service.execute_mutation(MutationInput(statement=statement, confirm_mutation=True))
    assert gateway.statements == []


@pytest.mark.anyio
async def test_list_spaces_returns_typed_page_and_marks_current(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult([{"Name": name} for name in ("a", "b", "c")], space="b")])
    service = NebulaService(settings, gateway)

    output = await service.list_spaces(ListSpacesInput(limit=2, offset=1))

    assert gateway.statements == ["SHOW SPACES"]
    assert [(item.name, item.current) for item in output.spaces] == [("b", True), ("c", False)]
    assert (output.returned_count, output.total_count, output.current_space) == (2, 3, "b")


def schema_results(ddl: str = "CREATE TAG `player` (`name` string NULL)") -> list[FakeResult]:
    fields = [{"Field": "name", "Type": "string", "Null": "YES", "Default": None, "Comment": None}]
    return [
        FakeResult([{"ID": 1, "Name": "nba", "Partition Number": 10, "Replica Factor": 1,
                     "Vid Type": "FIXED_STRING(32)", "Comment": None}], space="nba"),
        FakeResult([{"Name": "player"}], space="nba"),
        FakeResult(fields, space="nba"),
        FakeResult([{"Tag": "player", "Create Tag": ddl}], space="nba"),
        FakeResult([{"Name": "follow"}], space="nba"),
        FakeResult([{"Field": "degree", "Type": "int64", "Null": "NO", "Default": 0,
                     "Comment": "weight"}], space="nba"),
        FakeResult([{"Edge": "follow", "Create Edge": "CREATE EDGE `follow` (`degree` int64)"}],
                   space="nba"),
        FakeResult([{"Index Name": "player_name", "By Tag": "player", "Columns": ["name"]}],
                   space="nba"),
        FakeResult(columns=["Index Name", "By Edge", "Columns"], space="nba"),
    ]


@pytest.mark.anyio
async def test_schema_reads_tags_edges_indexes_and_restores_prior_space(settings: Settings) -> None:
    gateway = FakeGateway(schema_results())
    service = NebulaService(settings, gateway)
    service.current_space = "other"

    output = await service.get_space_schema(SpaceSchemaInput(space="nba", include_ddl=True))

    assert gateway.statements == [
        "USE `nba`", "DESCRIBE SPACE `nba`",
        "SHOW TAGS", "DESCRIBE TAG `player`", "SHOW CREATE TAG `player`",
        "SHOW EDGES", "DESCRIBE EDGE `follow`", "SHOW CREATE EDGE `follow`",
        "SHOW TAG INDEXES", "SHOW EDGE INDEXES", "USE `other`",
    ]
    assert (output.vid_type, output.partition_num, output.replica_factor) == ("FIXED_STRING(32)", 10, 1)
    assert output.tags[0].name == "player"
    assert output.tags[0].properties[0].nullable is True
    assert output.edges[0].properties[0].model_dump() == {
        "name": "degree", "type": "int64", "nullable": False, "default": 0, "comment": "weight",
    }
    assert output.tags[0].ddl.startswith("CREATE TAG")
    assert output.indexes[0].model_dump() == {
        "kind": "tag", "name": "player_name", "schema_name": "player", "columns": ["name"],
    }
    assert output.session_space == "other"
    assert service.current_space == "other"


@pytest.mark.anyio
async def test_schema_ddl_is_utf8_bounded(settings: Settings) -> None:
    gateway = FakeGateway(schema_results("CREATE TAG `球员` " + "x" * 100))
    service = NebulaService(settings, gateway)

    output = await service.get_space_schema(
        SpaceSchemaInput(space="nba", include_ddl=True, max_ddl_bytes=20)
    )

    assert output.ddl_truncated is True
    assert len(output.tags[0].ddl.encode("utf-8")) <= 20
    assert output.edges[0].ddl is None
    assert service.current_space == "nba"


@pytest.mark.anyio
async def test_schema_for_unknown_space_returns_database_status(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult(error=SPACE_NOT_FOUND)])

    output = await NebulaService(settings, gateway).get_space_schema(SpaceSchemaInput(space="old"))

    assert output.status.ok is False
    assert output.status.code == "-1005"
    assert output.tags == [] and output.edges == []


@pytest.mark.anyio
async def test_validate_explain_never_executes_the_original_statement(settings: Settings) -> None:
    gateway = FakeGateway([FakeResult(plan_desc=plan(node(1, "Start", None)))])
    service = NebulaService(settings, gateway)
    service.current_space = "prior"

    output = await service.validate_ngql(ValidateNGQLInput(
        statement="PROFILE MATCH (n) RETURN n LIMIT 1", space="demo", run_explain=True,
    ))

    assert gateway.statements == ["USE `demo`", "EXPLAIN MATCH (n) RETURN n LIMIT 1", "USE `prior`"]
    assert output.evidence.explain_checked is True
    assert output.evidence.executed is False
    assert output.explain is not None and output.explain.operators[0]["name"] == "Start"


@pytest.mark.anyio
async def test_validate_without_explain_does_not_touch_database(settings: Settings) -> None:
    gateway = FakeGateway()

    output = await NebulaService(settings, gateway).validate_ngql(
        ValidateNGQLInput(statement="MATCH (n) RETURN n")
    )

    assert gateway.statements == []
    assert output.evidence.warnings[0].code == "missing_limit"
    assert output.explain is None


@pytest.mark.anyio
async def test_validate_explain_rejects_mutations_and_composites(settings: Settings) -> None:
    gateway = FakeGateway()
    service = NebulaService(settings, gateway)

    for statement in ('DELETE VERTEX "a"', "RETURN 1; RETURN 2"):
        with pytest.raises(NebulaMCPError):
            await service.validate_ngql(ValidateNGQLInput(statement=statement, run_explain=True))
    assert gateway.statements == []
