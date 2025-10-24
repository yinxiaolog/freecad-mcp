import FreeCAD
import FreeCADGui
import ObjectsFem

import contextlib
import queue
import base64
import io
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

from PySide import QtCore

from .parts_library import get_parts_list, insert_part_from_library
from .serialize import serialize_object

rpc_server_thread = None
rpc_server_instance = None

# GUI task queue
rpc_request_queue = queue.Queue()
rpc_response_queue = queue.Queue()


def process_gui_tasks():
    while not rpc_request_queue.empty():
        task = rpc_request_queue.get()
        try:
            res = task()
            if res is not None:
                rpc_response_queue.put(res)
        except Exception as e:
            # 捕获任务执行中的任何未处理异常
            error_msg = f"Unhandled exception in GUI task: {e}"
            FreeCAD.Console.PrintError(error_msg + "\n")
            rpc_response_queue.put(error_msg)

    QtCore.QTimer.singleShot(500, process_gui_tasks)


@dataclass
class Object:
    name: str
    type: str | None = None
    analysis: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


def set_object_property(
    doc: FreeCAD.Document, obj: FreeCAD.DocumentObject, properties: dict[str, Any]
):
    for prop, val in properties.items():
        try:
            if prop in obj.PropertiesList:
                if prop == "Placement" and isinstance(val, dict):
                    pos = val.get("Base", val.get("Position", {}))
                    rot = val.get("Rotation", {})
                    placement = FreeCAD.Placement(
                        FreeCAD.Vector(pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)),
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
                elif isinstance(getattr(obj, prop), FreeCAD.Vector) and isinstance(val, dict):
                    vector = FreeCAD.Vector(val.get("x", 0), val.get("y", 0), val.get("z", 0))
                    setattr(obj, prop, vector)
                elif prop in ["Base", "Tool", "Source", "Profile"] and isinstance(val, str):
                    ref_obj = doc.getObject(val)
                    if ref_obj:
                        setattr(obj, prop, ref_obj)
                    else:
                        raise ValueError(f"Referenced object '{val}' not found.")
                elif prop == "References" and isinstance(val, list):
                    refs = []
                    for ref_name, face in val:
                        ref_obj = doc.getObject(ref_name)
                        if ref_obj:
                            refs.append((ref_obj, face))
                        else:
                            raise ValueError(f"Referenced object '{ref_name}' not found.")
                    setattr(obj, prop, refs)
                else:
                    setattr(obj, prop, val)
            elif prop == "ShapeColor" and isinstance(val, (list, tuple)) and obj.ViewObject:
                setattr(obj.ViewObject, prop, tuple(float(c) for c in val))
            elif prop == "ViewObject" and isinstance(val, dict) and obj.ViewObject:
                for k, v in val.items():
                    if k == "ShapeColor":
                        setattr(obj.ViewObject, k, tuple(float(c) for c in v))
                    else:
                        setattr(obj.ViewObject, k, v)
            else:
                setattr(obj, prop, val)
        except Exception as e:
            # 抛出异常，由外层捕获并统一处理
            raise AttributeError(f"Property '{prop}' assignment error: {e}")


class FreeCADRPC:
    """RPC server for FreeCAD"""

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        rpc_request_queue.put(lambda: self._create_document_gui(name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "document_name": name}
        else:
            return {"success": False, "error": res}

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        try:
            obj = Object(
                name=obj_data.get("Name", "New_Object"),
                type=obj_data["Type"],
                analysis=obj_data.get("Analysis", None),
                properties=obj_data.get("Properties", {}),
            )
        except KeyError as e:
            return {"success": False, "error": f"Missing required field in obj_data: {e}"}

        rpc_request_queue.put(lambda: self._create_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def edit_object(
        self, doc_name: str, obj_name: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            properties=properties.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._edit_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def delete_object(self, doc_name: str, obj_name: str):
        rpc_request_queue.put(lambda: self._delete_object_gui(doc_name, obj_name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj_name}
        else:
            return {"success": False, "error": res}

    def execute_code(self, code: str) -> dict[str, Any]:
        output_buffer = io.StringIO()

        def task():
            try:
                with contextlib.redirect_stdout(output_buffer):
                    exec(code, globals())
                FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
                return True
            except Exception as e:
                FreeCAD.Console.PrintError(f"Error executing Python code: {e}\n")
                return f"Error executing Python code: {e}\n"

        rpc_request_queue.put(task)
        res = rpc_response_queue.get()
        if res is True:
            return {
                "success": True,
                "message": "Python code execution scheduled. \nOutput: "
                + output_buffer.getvalue(),
            }
        else:
            return {"success": False, "error": res}

    def get_objects(self, doc_name):
        try:
            doc = FreeCAD.getDocument(doc_name)
            return [serialize_object(obj) for obj in doc.Objects]
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_object(self, doc_name, obj_name):
        try:
            doc = FreeCAD.getDocument(doc_name)
            obj = doc.getObject(obj_name)
            if obj:
                return serialize_object(obj)
            else:
                return {"success": False, "error": f"Object '{obj_name}' not found in document '{doc_name}'."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def insert_part_from_library(self, relative_path):
        rpc_request_queue.put(lambda: self._insert_part_from_library(relative_path))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "message": "Part inserted from library."}
        else:
            return {"success": False, "error": res}

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def get_parts_list(self):
        return get_parts_list()

    def get_active_screenshot(self, view_name: str = "Isometric") -> str | None:
        def screenshot_task():
            try:
                # 检查是否有活动文档
                if not FreeCADGui.ActiveDocument:
                    return "No active document found to take a screenshot."
                
                view = FreeCADGui.ActiveDocument.ActiveView
                if not view or not hasattr(view, "saveImage"):
                    return "Current view does not support screenshots."

                fd, tmp_path = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                
                try:
                    self._save_active_screenshot(tmp_path, view_name)
                    with open(tmp_path, "rb") as image_file:
                        image_bytes = image_file.read()
                    return base64.b64encode(image_bytes).decode("utf-8")
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
            except Exception as e:
                return f"Failed to capture screenshot: {e}"

        rpc_request_queue.put(screenshot_task)
        res = rpc_response_queue.get()

        # 如果结果是字符串，说明是错误信息
        if isinstance(res, str) and not res.startswith(('iVBOR', 'R0lGOD', '/9j/')):
            FreeCAD.Console.PrintWarning(f"Screenshot error: {res}\n")
            return None
        return res

    def _create_document_gui(self, name):
        try:
            doc = FreeCAD.newDocument(name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Document '{name}' created via RPC.\n")
            return True
        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to create document '{name}': {e}\n")
            return f"Failed to create document: {e}"

    def _create_object_gui(self, doc_name, obj: Object):
        try:
            doc = FreeCAD.getDocument(doc_name)
            
            if obj.type == "Fem::FemMeshGmsh" and obj.analysis:
                from femmesh.gmshtools import GmshTools
                res = getattr(doc, obj.analysis).addObject(ObjectsFem.makeMeshGmsh(doc, obj.name))[0]
                if "Part" in obj.properties:
                    target_obj = doc.getObject(obj.properties['Part'])
                    if target_obj:
                        res.Part = target_obj
                    else:
                        raise ValueError(f"Referenced object '{obj.properties['Part']}' not found.")
                    del obj.properties["Part"]
                else:
                    raise ValueError("'Part' property not found for FemMeshGmsh.")
                
                for param, value in obj.properties.items():
                    if hasattr(res, param):
                        setattr(res, param, value)
                doc.recompute()
                gmsh_tools = GmshTools(res)
                gmsh_tools.create_mesh()
                FreeCAD.Console.PrintMessage(f"FEM Mesh '{res.Name}' generated in '{doc_name}'.\n")
            
            elif obj.type.startswith("Fem::"):
                fem_make_methods = {
                    "MaterialCommon": ObjectsFem.makeMaterialSolid,
                    "AnalysisPython": ObjectsFem.makeAnalysis,
                }
                obj_type_short = obj.type.split("::")[1]
                method_name = "make" + obj_type_short
                make_method = fem_make_methods.get(obj_type_short, getattr(ObjectsFem, method_name, None))
                
                if callable(make_method):
                    res = make_method(doc, obj.name)
                    set_object_property(doc, res, obj.properties)
                    if obj.type != "Fem::AnalysisPython" and obj.analysis:
                        getattr(doc, obj.analysis).addObject(res)
                    FreeCAD.Console.PrintMessage(f"FEM object '{res.Name}' created with '{method_name}'.\n")
                else:
                    raise ValueError(f"No creation method '{method_name}' found in ObjectsFem.")
            
            else:
                res = doc.addObject(obj.type, obj.name)
                set_object_property(doc, res, obj.properties)
                FreeCAD.Console.PrintMessage(f"{res.TypeId} '{res.Name}' added to '{doc_name}' via RPC.\n")

            doc.recompute()
            return True
        except Exception as e:
            error_msg = f"Failed to create object '{obj.name}' in '{doc_name}': {e}"
            FreeCAD.Console.PrintError(error_msg + "\n")
            return error_msg

    def _edit_object_gui(self, doc_name: str, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(
                f"Object '{obj.name}' not found in document '{doc_name}'.\n"
            )
            return f"Object '{obj.name}' not found in document '{doc_name}'.\n"

        try:
            # For Fem::ConstraintFixed
            if hasattr(obj_ins, "References") and "References" in obj.properties:
                refs = []
                for ref_name, face in obj.properties["References"]:
                    ref_obj = doc.getObject(ref_name)
                    if ref_obj:
                        refs.append((ref_obj, face))
                    else:
                        raise ValueError(f"Referenced object '{ref_name}' not found.")
                obj_ins.References = refs
                FreeCAD.Console.PrintMessage(
                    f"References updated for '{obj.name}' in '{doc_name}'.\n"
                )
                # delete References from properties
                del obj.properties["References"]
            set_object_property(doc, obj_ins, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _delete_object_gui(self, doc_name: str, obj_name: str):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        try:
            doc.removeObject(obj_name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj_name}' deleted via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _insert_part_from_library(self, relative_path):
        try:
            insert_part_from_library(relative_path)
            return True
        except Exception as e:
            return str(e)

    def _save_active_screenshot(self, save_path: str, view_name: str = "Isometric"):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            # Check if the view supports screenshots
            if not hasattr(view, "saveImage"):
                return "Current view does not support screenshots"

            if view_name == "Isometric":
                view.viewIsometric()
            elif view_name == "Front":
                view.viewFront()
            elif view_name == "Top":
                view.viewTop()
            elif view_name == "Right":
                view.viewRight()
            elif view_name == "Back":
                view.viewBack()
            elif view_name == "Left":
                view.viewLeft()
            elif view_name == "Bottom":
                view.viewBottom()
            elif view_name == "Dimetric":
                view.viewDimetric()
            elif view_name == "Trimetric":
                view.viewTrimetric()
            else:
                raise ValueError(f"Invalid view name: {view_name}")
            view.fitAll()
            view.saveImage(save_path, 1)
            return True
        except Exception as e:
            return str(e)


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

    QtCore.QTimer.singleShot(500, process_gui_tasks)

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
