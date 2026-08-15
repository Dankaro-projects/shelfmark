import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_registry_package_launches_the_serve_subcommand():
    metadata = json.loads((Path(__file__).parents[1] / "server.json").read_text())
    package = metadata["packages"][0]

    assert package["registryType"] == "pypi"
    assert package["identifier"] == "shelfmark"
    assert package["runtimeHint"] == "uvx"
    assert package["packageArguments"] == [
        {"type": "positional", "value": "serve"}
    ]


def test_registry_command_completes_an_mcp_handshake(config_path, built):
    async def exercise_server():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m", "shelfmark.cli", "--config", str(config_path),
                "serve", "--no-auto-refresh",
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                stats = await session.call_tool("corpus_stats")
                return initialized, tools, stats

    initialized, tools, stats = asyncio.run(exercise_server())

    assert initialized.serverInfo.name == "shelfmark"
    assert initialized.serverInfo.version == "0.4.9"
    assert {tool.name for tool in tools.tools} == {
        "browse_folder", "corpus_stats", "get_file", "search_docs",
        "search_emails",
    }
    assert "shelfmark corpus" in stats.content[0].text
