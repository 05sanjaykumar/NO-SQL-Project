"""MCP server exposing the live fraud detection system as callable tools.

`server` here is the submodule; the MCPServer instance lives at
`fraudsim.mcp_server.server.server`. Re-exporting the instance under the same
name would shadow the module and break `import fraudsim.mcp_server.server`.
"""

from fraudsim.mcp_server.server import main

__all__ = ["main"]
