import logging
import xmlrpc.client
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from mcp.server.fastmcp import FastMCP, Context, Image

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FreeCADMCPserver")


class FreeCADConnection:
    def __init__(self, host: str = "localhost", port: int = 9875):
        self.server = xmlrpc.client.ServerProxy(f"http://{host}:{port}")

    def ping(self) -> bool:
        return self.server.ping()

    def create_document(self, name: str) -> dict[str, Any]:
        return self.server.create_document(name)

    def create_object(self, doc_name: str, obj_data: dict[str, Any]) -> dict[str, Any]:
        return self.server.create_object(doc_name, obj_data)

    def edit_object(self, doc_name: str, obj_name: str, obj_data: dict[str, Any]) -> dict[str, Any]:
        return self.server.edit_object(doc_name, obj_name, obj_data)

    def execute_code(self, code: str) -> dict[str, Any]:
        return self.server.execute_code(code)

    def get_objects(self, doc_name: str) -> list[dict[str, Any]]:
        return self.server.get_objects(doc_name)

    def get_object(self, doc_name: str, obj_name: str) -> dict[str, Any]:
        return self.server.get_object(doc_name, obj_name)


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    try:
        logger.info("FreeCADMCP server starting up")
        try:
            _ = get_freecad_connection()
            logger.info("Successfully connected to FreeCAD on startup")
        except Exception as e:
            logger.warning(f"Could not connect to FreeCAD on startup: {str(e)}")
            logger.warning(
                "Make sure the FreeCAD addon is running before using FreeCAD resources or tools"
            )
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _freecad_connection
        if _freecad_connection:
            logger.info("Disconnecting from FreeCAD on shutdown")
            _freecad_connection.disconnect()
            _freecad_connection = None
        logger.info("FreeCADMCP server shut down")


mcp = FastMCP(
    "FreeCADMCP",
    description="FreeCAD integration through the Model Context Protocol",
    lifespan=server_lifespan,
)


_freecad_connection: FreeCADConnection | None = None


def get_freecad_connection():
    """Get or create a persistent FreeCAD connection"""
    global _freecad_connection
    if _freecad_connection is None:
        _freecad_connection = FreeCADConnection(host="localhost", port=9875)
        if not _freecad_connection.ping():
            logger.error("Failed to ping FreeCAD")
            _freecad_connection = None
            raise Exception(
                "Failed to connect to FreeCAD. Make sure the FreeCAD addon is running."
            )
    return _freecad_connection


@mcp.tool()
def create_document(ctx: Context, name: str) -> str:
    """Create a new document in FreeCAD.

    Args:
        name: The name of the document to create.

    Returns:
        A message indicating the success or failure of the document creation.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.create_document(name)
        return f"Document '{res['document_name']}' created successfully"
    except Exception as e:
        logger.error(f"Failed to create document: {str(e)}")
        return f"Failed to create document: {str(e)}"


@mcp.tool()
def create_object(
    ctx: Context,
    doc_name: str,
    obj_type: str,
    obj_name: str,
    obj_properties: dict[str, Any] = None,
) -> str:
    """Create a new object in FreeCAD.

    Args:
        doc_name: The name of the document to create the object in.
        obj_type: The type of the object to create (e.g. 'Part::Box', 'Part::Cylinder', 'Draft::Circle', 'PartDesign::Body', etc.).
        obj_name: The name of the object to create.
        obj_properties: The properties of the object to create.

    Returns:
        A message indicating the success or failure of the object creation.
    """
    freecad = get_freecad_connection()
    try:
        obj_data = {"Name": obj_name, "Type": obj_type, "Properties": obj_properties}
        res = freecad.create_object(doc_name, obj_data)
        return f"Object '{res['object_name']}' created successfully"
    except Exception as e:
        logger.error(f"Failed to create object: {str(e)}")
        return f"Failed to create object: {str(e)}"


@mcp.tool()
def edit_object(
    ctx: Context, doc_name: str, obj_name: str, obj_data: dict[str, Any]
) -> str:
    """Edit an object in FreeCAD.

    Args:
        doc_name: The name of the document to edit the object in.
        obj_name: The name of the object to edit.

        obj_data: The properties of the object to edit.

    Returns:
        A message indicating the success or failure of the object editing.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.edit_object(doc_name, obj_name, obj_data)
        return f"Object '{res['object_name']}' edited successfully"
    except Exception as e:
        logger.error(f"Failed to edit object: {str(e)}")
        return f"Failed to edit object: {str(e)}"


@mcp.tool()
def execute_code(ctx: Context, code: str) -> str:
    """Execute arbitrary Python code in FreeCAD.

    Args:
        code: The Python code to execute.

    Returns:
        A message indicating the success or failure of the code execution.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.execute_code(code)
        return f"Code executed successfully: {res['message']}"
    except Exception as e:
        logger.error(f"Failed to execute code: {str(e)}")
        return f"Failed to execute code: {str(e)}"


@mcp.tool()
def get_objects(ctx: Context, doc_name: str) -> list[dict[str, Any]]:
    """Get all objects in a document.

    Args:
        doc_name: The name of the document to get the objects from.

    Returns:
        A list of objects in the document.
    """
    freecad = get_freecad_connection()
    try:
        return freecad.get_objects(doc_name)
    except Exception as e:
        logger.error(f"Failed to get objects: {str(e)}")
        return []


@mcp.tool()
def get_object(ctx: Context, doc_name: str, obj_name: str) -> dict[str, Any]:
    """Get an object from a document.

    Args:
        doc_name: The name of the document to get the object from.
        obj_name: The name of the object to get.

    Returns:
        The object.
    """
    freecad = get_freecad_connection()
    try:
        return freecad.get_object(doc_name, obj_name)
    except Exception as e:
        logger.error(f"Failed to get object: {str(e)}")
        return None


def main():
    """Run the MCP server"""
    mcp.run()
