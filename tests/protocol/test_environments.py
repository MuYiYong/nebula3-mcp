import json

import pytest
from mcp import Client

from nebula3_mcp import server as server_module
from nebula3_mcp.errors import NebulaMCPError
from nebula3_mcp.server import create_server
from tests.fakes import FakeGateway, FakeResult


@pytest.mark.anyio
async def test_switch_environment_rolls_back_and_changes_connection(monkeypatch):
    monkeypatch.setenv('NEBULA_ENVIRONMENTS', json.dumps({
        name: {'NEBULA_ADDRESSES': host + ':9669', 'NEBULA_USERNAME': 'reader',
               'NEBULA_PASSWORD': 'env-secret'}
        for name, host in [('dev', 'dev'), ('prod', 'prod'), ('broken', 'bad')]
    }))
    monkeypatch.setenv('NEBULA_ENVIRONMENT', 'dev')
    gateways = []

    class EnvironmentGateway(FakeGateway):
        def __init__(self, settings):
            super().__init__()
            self.settings, self.closed = settings, False
            gateways.append(self)

        def open(self):
            if self.settings.addresses.startswith('bad'):
                raise NebulaMCPError(category='connection_error', message='Cannot connect')

        def close(self):
            self.closed = True

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult([{'n': 1}])

    monkeypatch.setattr(server_module, 'DatabaseGateway', EnvironmentGateway)
    async with Client(create_server()) as client:
        listed = await client.call_tool('nebula_list_environments', {})
        assert listed.structured_content['active'] == 'dev'
        assert 'env-secret' not in str(listed)
        first = await client.call_tool('nebula_execute_query', {'statement': 'RETURN 1'})
        token = first.structured_content['query']['connection_id']
        switched = await client.call_tool('nebula_switch_environment', {'name': 'prod'})
        assert not switched.is_error
        assert gateways[0].closed
        failed = await client.call_tool('nebula_switch_environment', {'name': 'broken'})
        assert failed.is_error
        assert gateways[-1].closed
        assert not gateways[1].closed
        listed = await client.call_tool('nebula_list_environments', {})
        assert listed.structured_content['active'] == 'prod'
        second = await client.call_tool('nebula_execute_query', {'statement': 'RETURN 2'})
        assert second.structured_content['query']['environment'] == 'prod'
        assert second.structured_content['query']['connection_id'] != token
    assert gateways[1].closed


@pytest.mark.anyio
async def test_invalid_environment_config_does_not_fall_back_or_leak(monkeypatch):
    monkeypatch.setenv('NEBULA_ENVIRONMENTS', '{env-secret')
    async with Client(create_server()) as client:
        result = await client.call_tool('nebula_execute_query', {'statement': 'RETURN 1'})
        assert result.is_error
        assert 'env-secret' not in str(result)
        assert result.structured_content['error']['code'] == 'INVALID_ENVIRONMENTS'


@pytest.mark.anyio
async def test_flat_native_server_uses_its_environment_name(monkeypatch, settings):
    monkeypatch.delenv('NEBULA_ENVIRONMENTS', raising=False)
    monkeypatch.setenv('NEBULA_ENVIRONMENT', 'secondary')
    monkeypatch.setenv('NEBULA_ADDRESSES', settings.addresses)
    monkeypatch.setenv('NEBULA_USERNAME', settings.username)
    monkeypatch.setenv('NEBULA_PASSWORD', settings.password.get_secret_value())

    class FlatGateway(FakeGateway):
        def __init__(self, config):
            super().__init__([FakeResult([{'probe': 1}])])

        def open(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(server_module, 'DatabaseGateway', FlatGateway)
    async with Client(create_server()) as client:
        listed = await client.call_tool('nebula_list_environments', {})
        assert listed.structured_content['active'] == 'secondary'
        result = await client.call_tool('nebula_execute_query', {'statement': 'RETURN 1 AS probe'})
        assert result.structured_content['query']['environment'] == 'secondary'
