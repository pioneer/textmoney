"""Query the configured stdio Flet MCP for APIs used by the PoC."""

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server = StdioServerParameters(
        command="uv",
        args=["run", "flet", "mcp"],
        env=os.environ | {"FLET_MCP_ENABLE_API": "1", "FLET_MCP_ENABLE_CLI": "1"},
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name, query in [
            ("FilePicker", "get_directory_path"),
            ("Service", ""),
            ("Page", "run_task"),
            ("Page", "on_disconnect"),
            ("SharedPreferences", ""),
            ("TextField", "on_change"),
            ("Button", "on_click"),
            ("BasePage", "show_dialog"),
            ("BasePage", "pop_dialog"),
            ("AlertDialog", ""),
            ("TextButton", "on_click"),
        ]:
            result = await session.call_tool("get_api", {"name": name, "query": query})
            print(f"\n{name} {query}: {result.model_dump_json()}")
        result = await session.call_tool("get_cli_help", {"command": "build"})
        print(f"\nBuild CLI: {result.model_dump_json()}")


if __name__ == "__main__":
    asyncio.run(main())
