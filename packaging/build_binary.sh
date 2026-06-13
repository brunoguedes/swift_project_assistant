#!/usr/bin/env bash
# Build a standalone single-file binary for the swift-project-assistant MCP
# server using PyInstaller. The MCP server only needs mcp, python-dotenv and
# httpx at runtime (SourceKitten and the optional `claude` CLI are external
# binaries it shells out to), so the resulting executable is small.
#
# Usage:  poetry run packaging/build_binary.sh
# Output: dist/swift-project-mcp
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

pyinstaller \
  --onefile \
  --name swift-project-mcp \
  --paths src \
  --collect-submodules mcp \
  --collect-submodules swift_project_assistant \
  --clean --noconfirm \
  packaging/entry.py

echo
echo "Built: $ROOT/dist/swift-project-mcp"
