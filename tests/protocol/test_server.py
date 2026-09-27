from __future__ import annotations

import json
from copy import deepcopy

import pytest
from mcp import Client

from nebula3_mcp import server as server_module
from nebula3_mcp.config import Settings
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.server import create_server
from nebula3_mcp.service import NebulaService
from tests.fakes import FakeResult

EXPECTED_TOOLS = {
    "nebula_list_environments",
    "nebula_switch_environment",
    "nebula_test_connection",
    "nebula_list_spaces",
    "nebula_get_space_schema",
    "nebula_validate_ngql",
    "nebula_execute_query",
    "nebula_execute_mutation",
    "nebula_render_result",
    "nebula_configure_connection",
    "nebula_select_space",
}


class RoutingGateway:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def version(self) -> str:
        return "3.8.0"

    async def execute(self, statement: str) -> FakeResult:
        self.statements.append(statement)
        if statement.startswith("USE "):
            return FakeResult(space="demo")
        if statement == "SHOW SPACES":
            return FakeResult([{"Name": "demo"}])
        if statement == "SHOW TAGS":
            return FakeResult([{"Name": "player"}], space="demo")
        if statement.startswith("DESCRIBE TAG"):
            return FakeResult([{"Field": "name", "Type": "string", "Null": "YES",
                                "Default": None, "Comment": None}], space="demo")
        if statement.startswith(("SHOW EDGES", "SHOW TAG INDEXES", "SHOW EDGE INDEXES")):
            return FakeResult(space="demo")
        if statement.startswith("DESCRIBE SPACE"):
            return FakeResult([{"Vid Type": "FIXED_STRING(32)"}], space="demo")
        return FakeResult([{"sector": "A", "score": 1.0}])


@pytest.fixture
def service(settings: Settings) -> NebulaService:
    return NebulaService(settings, RoutingGateway())


@pytest.mark.anyio
async def test_server_exposes_complete_prefixed_tool_set(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        server_version = client.server_info.version

    assert set(tools) == EXPECTED_TOOLS
    assert server_version == "0.1.0"
    assert tools["nebula_execute_query"].annotations.read_only_hint is True
    assert tools["nebula_execute_query"].annotations.destructive_hint is False
    assert tools["nebula_execute_mutation"].annotations.destructive_hint is True
    assert tools["nebula_render_result"].meta == {
        "ui": {"resourceUri": "ui://nebula3/query-result.html"}
    }
    assert "space" in tools["nebula_execute_query"].input_schema["properties"]
    assert "graph" not in tools["nebula_execute_query"].input_schema["properties"]
    assert all(tool.output_schema is not None for tool in tools.values())


@pytest.mark.anyio
async def test_query_result_ui_resource_is_registered(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        resources = await client.list_resources()
        resource = await client.read_resource("ui://nebula3/query-result.html")

    listed = {str(item.uri): item for item in resources.resources}
    assert listed["ui://nebula3/query-result.html"].mime_type == "text/html;profile=mcp-app"
    assert listed["ui://nebula3/query-result.html"].meta == {"ui": {"prefersBorder": True}}
    assert resource.contents[0].mime_type == "text/html;profile=mcp-app"
    assert "复制 nGQL" in resource.contents[0].text


@pytest.mark.anyio
async def test_render_result_returns_identical_text_and_structured_content(
    service: NebulaService,
) -> None:
    async with Client(create_server(service=service)) as client:
        query = await client.call_tool("nebula_execute_query", {"statement": "RETURN 1"})
        payload = deepcopy(query.structured_content)
        payload["graph"]["space"] = "demo"
        payload["graph"]["elements"]["nodes"] = [
            {"data": {"id": "demo:9223372036854775807", "vid": "9223372036854775807",
                      "space": "demo", "properties": {"player.name": "Alice"}}}
        ]
        rendered = await client.call_tool(
            "nebula_render_result", {"result": payload, "explanation": "查询返回 Alice 顶点。"},
        )
        scalar = await client.call_tool(
            "nebula_render_result",
            {"result": query.structured_content, "explanation": "查询返回一行。"},
        )

    assert rendered.is_error is False
    assert json.loads(rendered.content[0].text) == rendered.structured_content
    assert rendered.structured_content["result"] == payload
    assert scalar.is_error is False


@pytest.mark.anyio
async def test_query_presentation_contract_is_delivered_to_mcp_clients(
    service: NebulaService,
) -> None:
    async with Client(create_server(service=service)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        instructions = client.instructions or ""

    normalized = " ".join(instructions.split()).lower()
    for required in (
        "present every enabled result component together",
        "render every non-empty vega-lite-v5 chart",
        "write a human-readable explanation",
        "do not rewrite explicit ngql merely to create a graph",
        "natural-language scalar request",
        "ngql-skills",
        "show ngql from query.display_statement",
        "switches it for later queries",
        "never use gql or graphql language tags",
    ):
        assert required.lower() in normalized, required

    query_tool = tools["nebula_execute_query"]
    assert "A table does not replace charts or the explanation" in (query_tool.description or "")
    for name in ("include_graph", "include_analysis", "include_charts"):
        assert query_tool.input_schema["properties"][name]["default"] is True
    output_properties = query_tool.output_schema["properties"]
    assert "render" in output_properties["charts"]["description"].lower()
    assert "human-readable explanation" in output_properties["explanation_context"]["description"]


@pytest.mark.anyio
async def test_successful_tool_returns_valid_structured_content(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        result = await client.call_tool("nebula_execute_query", {"statement": "RETURN 1"})

    content = result.structured_content
    assert result.is_error is False
    assert content["status"]["ok"] is True
    assert content["query"]["statement"] == "RETURN 1"
    assert content["query"]["executed_statement"] == "PROFILE RETURN 1"
    assert content["query"]["display_statement"] == "RETURN 1"
    assert content["graph"]["format"] == "cytoscape-elements-v1"
    assert content["charts"][0]["format"] == "vega-lite-v5"
    assert content["explanation_context"]["facts"] == [
        "Returned 1 row(s).",
        "Extracted 0 vertex/vertices and 0 edge(s).",
    ]


@pytest.mark.anyio
async def test_expected_service_error_is_safe_and_structured(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        result = await client.call_tool("nebula_execute_query", {"statement": "DROP SPACE demo"})

    assert result.is_error is True
    assert result.structured_content["error"]["category"] == "policy_denied"
    assert "runtime-secret" not in json.dumps(result.structured_content)


@pytest.mark.anyio
async def test_unconfigured_server_initializes_and_returns_variable_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("NEBULA_ADDRESSES", "NEBULA_USERNAME", "NEBULA_PASSWORD", "NEBULA_ENVIRONMENTS"):
        monkeypatch.delenv(name, raising=False)

    async with Client(create_server()) as client:
        tools = await client.list_tools()
        result = await client.call_tool("nebula_test_connection", {})
        resource = await client.read_resource("nebula3://connection")

    assert len(tools.tools) == len(EXPECTED_TOOLS)
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "CONFIGURATION_REQUIRED"
    assert error["variables"] == ["NEBULA_ADDRESSES", "NEBULA_PASSWORD", "NEBULA_USERNAME"]
    assert json.loads(resource.contents[0].text)["code"] == "CONFIGURATION_REQUIRED"


@pytest.mark.anyio
async def test_invalid_configuration_server_initializes_and_returns_variable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBULA_ENVIRONMENTS", raising=False)
    monkeypatch.setenv("NEBULA_ADDRESSES", "not-an-address")
    monkeypatch.setenv("NEBULA_USERNAME", "root")
    monkeypatch.setenv("NEBULA_PASSWORD", "secret")

    async with Client(create_server()) as client:
        result = await client.call_tool("nebula_test_connection", {})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "CONFIGURATION_REQUIRED"
    assert result.structured_content["error"]["variables"] == ["NEBULA_ADDRESSES"]


@pytest.mark.anyio
async def test_connection_failure_does_not_abort_initialization(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingGateway:
        def __init__(self, resolved: Settings) -> None:
            assert resolved == settings

        def open(self) -> None:
            raise NebulaMCPError(
                category="connection_error", message="Database connection failed", retryable=True,
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(server_module, "DatabaseGateway", FailingGateway)
    async with Client(create_server(settings=settings)) as client:
        result = await client.call_tool("nebula_list_spaces", {})
        resource = await client.read_resource("nebula3://connection")

    assert result.is_error is True
    assert result.structured_content["error"]["category"] == "connection_error"
    assert json.loads(resource.contents[0].text)["category"] == "connection_error"


@pytest.mark.anyio
async def test_connection_and_schema_resources_are_readable(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        connection = await client.read_resource("nebula3://connection")
        schema = await client.read_resource("nebula3://schema/demo")

    assert [str(item.uri) for item in resources.resources] == [
        "nebula3://connection",
        "ui://nebula3/query-result.html",
    ]
    assert [item.uri_template for item in templates.resource_templates] == [
        "nebula3://schema/{space}"
    ]
    assert json.loads(connection.contents[0].text)["version"] == "3.8.0"
    parsed = json.loads(schema.contents[0].text)
    assert parsed["tags"][0]["name"] == "player"
    assert parsed["vid_type"] == "FIXED_STRING(32)"


@pytest.mark.anyio
async def test_list_spaces_and_schema_tools(service: NebulaService) -> None:
    async with Client(create_server(service=service)) as client:
        spaces = await client.call_tool("nebula_list_spaces", {"limit": 5})
        schema = await client.call_tool("nebula_get_space_schema", {"space": "demo"})
        invalid = await client.call_tool("nebula_get_space_schema", {"space": "bad`name"})

    assert spaces.structured_content["spaces"] == [{"name": "demo", "current": False}]
    assert schema.structured_content["tags"][0]["properties"][0]["name"] == "name"
    assert invalid.is_error is True
