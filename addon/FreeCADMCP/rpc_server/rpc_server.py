import FreeCAD
import FreeCADGui

import queue
import threading
from dataclasses import dataclass
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

from PySide2.QtCore import QTimer

from .serialize import serialize_object

rpc_server_thread = None
rpc_server_instance = None

# GUI task queue
rpc_request_queue = queue.Queue()
rpc_response_queue = queue.Queue()


def process_gui_tasks():
    while not rpc_request_queue.empty():
        task = rpc_request_queue.get()
        res = task()
        rpc_response_queue.put(res)
    QTimer.singleShot(500, process_gui_tasks)


@dataclass
class Object:
    name: str
    type: str
    properties: dict[str, Any]


def set_object_property(
    doc: FreeCAD.Document, obj: FreeCAD.DocumentObject, properties: dict[str, Any]
):
    for prop in obj.PropertiesList:
        if prop in properties:
            val = properties[prop]
            try:
                if prop == "Placement" and isinstance(val, dict):
                    pos = val.get("Position", {})
                    rot = val.get("Rotation", {})
                    placement = FreeCAD.Placement(
                        FreeCAD.Vector(
                            pos.get("x", 0),
                            pos.get("y", 0),
                            pos.get("z", 0),
                        ),
                        FreeCAD.Rotation(
                            FreeCAD.Vector(
                                rot.get("Axis", {}).get("x", 0),
                                rot.get("Axis", {}).get("y", 0),
                                rot.get("Axis", {}).get("z", 1),
                            ),
                            rot.get("Angle", 0),
                        ),
                    )
                    setattr(obj, prop, placement)

                elif isinstance(getattr(obj, prop), FreeCAD.Vector) and isinstance(
                    val, dict
                ):
                    vector = FreeCAD.Vector(
                        val.get("x", 0), val.get("y", 0), val.get("z", 0)
                    )
                    setattr(obj, prop, vector)

                elif prop in ["Base", "Tool", "Source", "Profile"] and isinstance(
                    val, str
                ):
                    ref_obj = doc.getObject(val)
                    if ref_obj:
                        setattr(obj, prop, ref_obj)
                    else:
                        raise ValueError(f"Referenced object '{val}' not found.")

                else:
                    setattr(obj, prop, val)

            except Exception as e:
                FreeCAD.Console.PrintError(f"Property '{prop}' assignment error: {e}\n")


class FreeCADRPC:
    """RPC server for FreeCAD"""

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        rpc_request_queue.put(lambda: self._create_document_gui(name))
        res = rpc_response_queue.get()
        if res:
            return {"success": True, "document_name": name}
        else:
            return {"success": False, "error": "Failed to create document."}

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        obj = Object(
            name=obj_data.get("Name", "New_Object"),
            type=obj_data["Type"],
            properties=obj_data.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._create_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": "Failed to create object."}

    def edit_object(self, doc_name: str, obj_name: str, properties: dict[str, Any]) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            type=properties["Type"],
            properties=properties.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._edit_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": "Failed to edit object."}

    def execute_code(self, code: str) -> dict[str, Any]:
        def task():
            try:
                exec(code, globals())
                FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
                return None
            except Exception as e:
                FreeCAD.Console.PrintError(
                    f"Error executing Python code: {e}\n"
                )
                return str(e)
        rpc_request_queue.put(task)
        res = rpc_response_queue.get()
        if res is None:
            return {"success": True, "message": "Python code execution scheduled."}
        else:
            return {"success": False, "error": res}

    def get_objects(self, doc_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return [serialize_object(obj) for obj in doc.Objects]
        else:
            return []

    def get_object(self, doc_name, obj_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return serialize_object(doc.getObject(obj_name))
        else:
            return None

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def _create_document_gui(self, name):
        doc = FreeCAD.newDocument(name)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Document '{name}' created via RPC.\n")
        return doc
    def _create_object_gui(self, doc_name, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            res = doc.addObject(obj.type, obj.name)
            set_object_property(doc, res, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(
                f"{res.TypeId} '{res.Name}' added to '{doc_name}' via RPC.\n"
            )
            return res
        else:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return None

    def _edit_object_gui(self, doc_name: str, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return None

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(f"Object '{obj.name}' not found in document '{doc_name}'.\n")
            return None

        set_object_property(doc, obj_ins, obj.properties)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
        return obj_ins


def start_rpc_server(host="localhost", port=9875):
    global rpc_server_thread, rpc_server_instance

    if rpc_server_instance:
        return "RPC Server already running."

    rpc_server_instance = SimpleXMLRPCServer(
        (host, port), allow_none=True, logRequests=False
    )
    rpc_server_instance.register_instance(FreeCADRPC())

    def server_loop():
        FreeCAD.Console.PrintMessage(f"RPC Server started at {host}:{port}\n")
        rpc_server_instance.serve_forever()

    rpc_server_thread = threading.Thread(target=server_loop, daemon=True)
    rpc_server_thread.start()

    QTimer.singleShot(500, process_gui_tasks)

    return f"RPC Server started at {host}:{port}."


def stop_rpc_server():
    global rpc_server_instance, rpc_server_thread

    if rpc_server_instance:
        rpc_server_instance.shutdown()
        rpc_server_thread.join()
        rpc_server_instance = None
        rpc_server_thread = None
        FreeCAD.Console.PrintMessage("RPC Server stopped.\n")
        return "RPC Server stopped."

    return "RPC Server was not running."


class StartRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Start RPC Server", "ToolTip": "Start RPC Server"}

    def Activated(self):
        msg = start_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class StopRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Stop RPC Server", "ToolTip": "Stop RPC Server"}

    def Activated(self):
        msg = stop_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


FreeCADGui.addCommand("Start_RPC_Server", StartRPCServerCommand())
FreeCADGui.addCommand("Stop_RPC_Server", StopRPCServerCommand())
