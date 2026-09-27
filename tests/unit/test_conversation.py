from __future__ import annotations

import pytest

from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.models import QueryInput
from nebula3_mcp.service import NebulaService, leading_use
from tests.fakes import MISSING_SPACE, SPACE_NOT_FOUND, FakeGateway, FakeResult


def missing_space() -> FakeResult:
    return FakeResult(error=MISSING_SPACE)


def unknown_space() -> FakeResult:
    return FakeResult(error=SPACE_NOT_FOUND)


@pytest.mark.anyio
async def test_missing_space_retains_query_then_select_resumes_once(settings):
    gateway = FakeGateway([missing_space(), FakeResult([{"n": 1}], space="demo")])
    service = NebulaService(settings, gateway)

    with pytest.raises(NebulaMCPError) as caught:
        await service.execute_query(QueryInput(statement="MATCH (n) RETURN n", max_rows=3))
    assert caught.value.code == "SPACE_SELECTION_REQUIRED"
    assert caught.value.database_code == "-1009"

    output = await service.select_space("demo")

    assert output.session_statement == "USE `demo`"
    assert output.result.table.rows == [{"n": 1}]
    assert output.result.query.space == "demo"
    assert output.result.query.display_statement == "MATCH (n) RETURN n"
    assert gateway.statements == [
        "PROFILE MATCH (n) RETURN n", "USE `demo`", "PROFILE MATCH (n) RETURN n",
    ]
    assert (await service.select_space("other")).result is None


@pytest.mark.anyio
async def test_selection_failure_preserves_pending_and_previous_space(settings):
    gateway = FakeGateway([missing_space(), unknown_space(), FakeResult([{"n": 1}])])
    service = NebulaService(settings, gateway)
    with pytest.raises(NebulaMCPError):
        await service.execute_query(QueryInput(statement="MATCH (n) RETURN n"))

    with pytest.raises(NebulaMCPError) as caught:
        await service.select_space("missing")

    assert caught.value.code == "SPACE_SELECTION_REQUIRED"
    assert service.current_space is None
    assert (await service.select_space("demo")).result is not None


@pytest.mark.anyio
async def test_other_query_errors_do_not_become_pending(settings):
    gateway = FakeGateway([FakeResult(error=(-1004, "SyntaxError: syntax error near `x'"))])
    service = NebulaService(settings, gateway)

    output = await service.execute_query(QueryInput(statement="RETURN x y"))

    assert output.status.ok is False
    assert output.status.code == "-1004"
    assert (await service.select_space("demo")).result is None


@pytest.mark.anyio
@pytest.mark.parametrize("space", ["demo`; DROP SPACE demo", "a\nb", ""])
async def test_space_selection_cannot_inject_statements(settings, space):
    gateway = FakeGateway()
    with pytest.raises(NebulaMCPError):
        await NebulaService(settings, gateway).select_space(space)
    assert gateway.statements == []


@pytest.mark.anyio
async def test_unknown_explicit_space_is_replaced_on_resume(settings):
    gateway = FakeGateway([unknown_space(), FakeResult(space="new_space")], auto_use=False)
    gateway.auto_use = False
    service = NebulaService(settings, gateway)
    with pytest.raises(NebulaMCPError) as caught:
        await service.execute_query(QueryInput(statement="USE old; MATCH (n) RETURN n LIMIT 1"))
    assert caught.value.database_code == "-1005"
    assert gateway.statements == ["USE `old`"]
    gateway.auto_use = True
    gateway.results = [FakeResult([{"n": 1}], space="new_space")]

    result = (await service.select_space("new_space")).result

    assert result.query.display_statement == "USE `new_space`; MATCH (n) RETURN n LIMIT 1"
    assert result.query.space == "new_space"
    assert gateway.statements[-2:] == ["USE `new_space`", "PROFILE MATCH (n) RETURN n LIMIT 1"]


@pytest.mark.parametrize(
    ("statement", "space", "body"),
    [
        ("USE old; MATCH (n) RETURN n", "old", " MATCH (n) RETURN n"),
        ("USE `my space`;MATCH (n) RETURN n", "my space", "MATCH (n) RETURN n"),
        ("/* c */ USE old # note\n; RETURN 1", "old", " RETURN 1"),
        ("  use 篮球;\nRETURN 1", "篮球", "\nRETURN 1"),
        ("USE old", "old", ""),
    ],
)
def test_leading_use_locates_only_the_space_token(statement, space, body):
    clause = leading_use(statement)

    assert clause is not None
    assert clause.space == space
    assert statement[clause.body_start:] == body


@pytest.mark.parametrize("statement", ["MATCH (n) RETURN n", "EXPLAIN USE a; RETURN 1", "RETURN 1; USE a"])
def test_non_leading_use_is_not_a_session_prefix(statement):
    assert leading_use(statement) is None


@pytest.mark.anyio
async def test_server_reported_space_is_authoritative(settings):
    gateway = FakeGateway([FakeResult([{"n": 1}], space="reported")])
    service = NebulaService(settings.model_copy(update={"default_space": "configured"}), gateway)
    assert service.current_space == "configured"

    output = await service.execute_query(QueryInput(statement="RETURN 1 AS n"))

    assert output.query.space == "reported"
    assert service.current_space == "reported"
