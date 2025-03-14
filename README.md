# FreeCAD MCP

## Install addon

Addon directory is
* Windows: `%APPDATA%\FreeCAD\Mod\`
* Mac: `~/Library/Application Support/FreeCAD/Mod/`
* Linux: `~/.FreeCAD/Mod/` or `~/snap/freecad/common/Mod/` (if you install FreeCAD from snap)

When you install addon, you need to restart FreeCAD.
You can select "MCP Addon" from Workbench list and use it.

![workbench_list](./assets/workbench_list.png)

And you can start RPC server by "Start RPC Server" command in "FreeCAD MCP" toolbar.

![start_rpc_server](./assets/start_rpc_server.png)

## Setting up Claude Desktop

For user.

```json
{
    "mcpServers": {
        "freecad": {
            "command": "uvx",
            "args": [
                "freecad-mcp"
            ]
        }
    }
}
```

For developer.
First, you need clone this repository.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
```

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/freecad-mcp/",
        "run",
        "freecad-mcp"
      ]
    }
  }
}
```