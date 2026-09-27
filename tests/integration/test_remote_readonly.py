"""Live read-only checks against a NebulaGraph 3.8 cluster with the basketballplayer space.

Enable with NEBULA_RUN_REMOTE_TESTS=1 and NEBULA_ADDRESSES/USERNAME/PASSWORD.
"""

from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree

import pytest
from mcp import Client

from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.models import (
    ListSpacesInput,
    MutationInput,
    QueryInput,
    SpaceSchemaInput,
    ValidateNGQLInput,
)
from nebula3_mcp.server import create_server
from nebula3_mcp.service import NebulaService

pytestmark = pytest.mark.skipif(
    os.getenv("NEBULA_RUN_REMOTE_TESTS") != "1",
    reason="set NEBULA_RUN_REMOTE_TESTS=1 for NebulaGraph 3.8 integration",
)

SPACE = os.getenv("NEBULA_TEST_SPACE", "basketballplayer")
EVALUATIONS = Path(__file__).resolve().parents[2] / "evaluations/remote_readonly.xml"


@pytest.mark.anyio
async def test_remote_version_spaces_and_schema(remote_service: NebulaService) -> None:
    connection = await remote_service.test_connection()
    spaces = await remote_service.list_spaces(ListSpacesInput(limit=100))
    schema = await remote_service.get_space_schema(SpaceSchemaInput(space=SPACE, include_ddl=True))

    assert connection.connected is True
    assert connection.version.startswith("3.8")
    assert SPACE in {item.name for item in spaces.spaces}
    assert schema.status.ok is True
    assert schema.tags and schema.edges
    assert all(item.ddl and item.ddl.startswith("CREATE") for item in schema.tags)


@pytest.mark.anyio
async def test_remote_missing_space_is_resumed_after_selection(remote_service: NebulaService) -> None:
    if remote_service.current_space is not None:
        pytest.skip("the session already has a space")
    with pytest.raises(NebulaMCPError) as caught:
        await remote_service.execute_query(QueryInput(statement="MATCH (v) RETURN v LIMIT 1"))
    assert caught.value.code == "SPACE_SELECTION_REQUIRED"

    selected = await remote_service.select_space(SPACE)

    assert selected.result is not None
    assert selected.result.status.ok is True
    assert selected.result.graph is not None and len(selected.result.graph.elements.nodes) == 1
    assert remote_service.current_space == SPACE


@pytest.mark.anyio
async def test_remote_scalar_vertex_edge_path_and_profile_shapes(
    remote_service: NebulaService,
) -> None:
    scalar = await remote_service.execute_query(QueryInput(
        statement='RETURN 1 AS probe, date("2020-01-02") AS day',
        include_graph=False, include_analysis=False, include_charts=False,
    ))
    vertex = await remote_service.execute_query(QueryInput(
        statement=f"USE {SPACE}; MATCH (v:player) RETURN v LIMIT 1", include_charts=False,
    ))
    edge = await remote_service.execute_query(QueryInput(
        statement="MATCH (a)-[e:follow]->(b) RETURN e LIMIT 1", space=SPACE,
    ))
    path = await remote_service.execute_query(QueryInput(
        statement="MATCH p = (a:player)-[:follow]->(b) RETURN p LIMIT 1", space=SPACE,
    ))

    assert scalar.status.ok is True
    assert scalar.table.rows == [{"probe": 1, "day": {"$type": "date", "value": "2020-01-02"}}]
    assert vertex.query.session_statement == f"USE `{SPACE}`"
    assert vertex.query.executed_statement.startswith("PROFILE MATCH")
    assert vertex.graph is not None and len(vertex.graph.elements.nodes) == 1
    assert vertex.graph.elements.nodes[0].data["tags"] == ["player"]
    assert vertex.profile is not None and vertex.profile.operators
    assert edge.graph is not None and len(edge.graph.elements.edges) == 1
    ids = {item.data["id"] for item in edge.graph.elements.nodes}
    assert edge.graph.elements.edges[0].data["source"] in ids
    assert edge.graph.elements.edges[0].data["target"] in ids
    assert path.graph is not None and path.graph.paths[0]["hop_count"] == 1
    assert path.graph.paths[0]["sdk_length"] == 1


@pytest.mark.anyio
async def test_remote_native_ngql_statement_families(remote_service: NebulaService) -> None:
    await remote_service.select_space(SPACE)
    statements = [
        'GO FROM "player100" OVER follow YIELD edge AS e | LIMIT 3',
        'FETCH PROP ON player "player100" YIELD vertex AS v',
        "LOOKUP ON player YIELD id(vertex) AS id | LIMIT 3",
        'FIND SHORTEST PATH FROM "player100" TO "player101" OVER * YIELD path AS p',
        'GET SUBGRAPH 1 STEPS FROM "player100" YIELD VERTICES AS nodes, EDGES AS relationships',
        (
            '$a = GO FROM "player100" OVER follow YIELD dst(edge) AS id; '
            "GO FROM $a.id OVER follow YIELD dst(edge) AS d"
        ),
        "SHOW TAGS",
    ]

    for statement in statements:
        output = await remote_service.execute_query(QueryInput(statement=statement))
        assert output.status.ok is True, (statement, output.status)
    subgraph = await remote_service.execute_query(QueryInput(statement=statements[4]))
    assert subgraph.graph is not None and subgraph.graph.elements.edges


@pytest.mark.anyio
async def test_remote_explain_does_not_execute(remote_service: NebulaService) -> None:
    output = await remote_service.validate_ngql(ValidateNGQLInput(
        statement="MATCH (v:player) RETURN v LIMIT 1", space=SPACE, run_explain=True,
    ))

    assert output.evidence.explain_checked is True
    assert output.explain is not None and output.explain.operators
    assert all(item["rows"] is None for item in output.explain.operators)


@pytest.mark.anyio
async def test_remote_configuration_still_denies_mutation(remote_service: NebulaService) -> None:
    with pytest.raises(NebulaMCPError) as caught:
        await remote_service.execute_mutation(MutationInput(
            statement='INSERT VERTEX player(name, age) VALUES "never":("x", 1)',
            confirm_mutation=True,
        ))

    assert caught.value.category == "policy_denied"
    assert "disabled" in caught.value.message


@pytest.mark.anyio
async def test_remote_mcp_evaluation_answers(remote_service: NebulaService) -> None:
    root = ElementTree.parse(EVALUATIONS).getroot()
    if root.attrib["space"] != SPACE:
        pytest.skip("the XML evaluation targets another space")
    server = create_server(service=remote_service)

    async with Client(server) as client:
        for qa_pair in root.findall("qa_pair"):
            result = await client.call_tool("nebula_get_space_schema", {"space": SPACE})
            assert result.is_error is False
            facts = _schema_facts(result.structured_content)
            assert facts[qa_pair.attrib["fact_key"]] == qa_pair.findtext("expected_answer")


def _schema_facts(output: dict[str, object]) -> dict[str, str]:
    tags = {item["name"]: item for item in output["tags"]}  # type: ignore[union-attr]
    edges = {item["name"]: item for item in output["edges"]}  # type: ignore[union-attr]

    def names(item: dict[str, object]) -> str:
        return ",".join(sorted(prop["name"] for prop in item["properties"]))  # type: ignore[index, union-attr]

    return {
        "vid_type": str(output["vid_type"]),
        "tag_count": str(len(tags)),
        "tag_names": ",".join(sorted(tags)),
        "player_properties": names(tags["player"]),
        "player_age_type": next(
            prop["type"] for prop in tags["player"]["properties"] if prop["name"] == "age"
        ),
        "team_property_count": str(len(tags["team"]["properties"])),
        "edge_count": str(len(edges)),
        "edge_names": ",".join(sorted(edges)),
        "follow_properties": names(edges["follow"]),
        "serve_properties": names(edges["serve"]),
    }
