"""PyInstaller entry point for the swift-project-assistant MCP server.

PyInstaller needs a real script file to analyze; the console-script entry
defined in pyproject (`swift_project_assistant.mcp_server:main`) isn't a file.
This thin wrapper simply calls that same `main()`.
"""

from swift_project_assistant.mcp_server import main

if __name__ == "__main__":
    main()
