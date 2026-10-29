import asyncio
import sys
import uuid

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def test_stdio_discovery_structured_results_and_validation(tmp_path):
    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "flight_mcp", "--data-dir", str(tmp_path / "mcp"), "serve"], cwd=tmp_path)
        async with Client(parameters) as client:
            catalog = await client.list_tools()
            tools = {tool.name: tool for tool in catalog.tools}
            assert set(tools) == {"search_flights", "get_search_result", "cancel_search",
                                  "get_session_status", "start_login", "finish_login", "list_providers",
                                  "configure_bark", "get_bark_status", "test_bark", "push_search_result"}
            assert tools["search_flights"].input_schema["properties"]["routes"]
            assert tools["get_session_status"].output_schema is not None
            response = await client.call_tool("get_session_status")
            assert not response.is_error
            assert response.structured_content["status"] == "not_logged_in"
            assert "cookies" not in str(response.model_dump()).lower()
            providers = await client.call_tool("list_providers")
            assert len(providers.structured_content["providers"]) == 5
            config = await client.call_tool("configure_bark", {"device_key": "SYNTHETIC_BARK_KEY"})
            assert not config.is_error
            assert "SYNTHETIC_BARK_KEY" not in str(config.model_dump())
            bark_status = await client.call_tool("get_bark_status")
            assert bark_status.structured_content["notify_on_completion"] is True
            missing = await client.call_tool("get_search_result", {"job_id": str(uuid.uuid4())})
            assert missing.structured_content["status"] == "not_found"
            invalid = await client.call_tool("search_flights", {"routes": [
                {"origin": "首尔", "destination": "成都", "dates": ["2000-01-01"]}]})
            assert invalid.is_error
    asyncio.run(scenario())
