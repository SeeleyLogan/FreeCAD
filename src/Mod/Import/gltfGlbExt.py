# -*- coding: utf8 -*-
"""
GLTF/GLB exporter patch with material name preservation and VarSet metadata injection for FreeCAD
"""

import FreeCAD
import ImportGui
import json
import struct
import os


def traverse_objects_once(objects):
    """Single traversal to collect all objects, materials, and varsets"""

    all_objects = []
    materials = []
    varsets = {}
    matrices = {}

    stack = [(obj, obj.Label if hasattr(obj, "Label") else "Unknown") for obj in objects]
    visited = set()

    while stack:
        obj, path = stack.pop()
        obj_id = id(obj)

        if obj_id in visited:
            continue
        visited.add(obj_id)

        all_objects.append(obj)

        if hasattr(obj, "Material") and obj.Material and hasattr(obj.Material, "Name"):
            mat_name = obj.Material.Name
            if mat_name not in materials:
                materials.append(mat_name)
        elif (
            hasattr(obj, "ShapeMaterial")
            and obj.ShapeMaterial
            and hasattr(obj.ShapeMaterial, "Name")
        ):
            mat_name = obj.ShapeMaterial.Name
            if mat_name not in materials:
                materials.append(mat_name)

        resolved_obj, resolved_placement = resolve_link_chain(obj)
        bbox = None

        if hasattr(resolved_obj, "Shape"):
            bbox = calculate_bbox(resolved_obj.Shape)

        if (hasattr(obj, "TypeId") and obj.TypeId == "PartDesign::Body" and bbox is not None) or \
        (hasattr(resolved_obj, "TypeId") and resolved_obj.TypeId == "PartDesign::Body" and bbox is not None):
            bbox_placement = FreeCAD.Placement(bbox['center'], bbox['rotation'])
            world_to_bbox = bbox_placement.inverse()

            if obj.Label not in matrices:
                matrices[obj.Label] = placement_to_matrix(world_to_bbox)

        if ( 
            hasattr(obj, "TypeId")
            and obj.TypeId == "App::VarSet"
            and hasattr(obj, "PropertiesList")
        ):
            parent_name = path.split(".")[-1] if "." in path else path
            if parent_name not in varsets:
                varsets[parent_name] = {}

            for prop in obj.PropertiesList:
                if hasattr(obj, prop):
                    val = getattr(obj, prop)
                    if val is not None:
                        varsets[parent_name][prop] = str(val)

        current_path = f"{path}.{obj.Label}" if hasattr(obj, "Label") else path

        if hasattr(obj, "Group") and obj.Group:
            stack.extend((child, current_path) for child in obj.Group)
        if hasattr(obj, "Objects") and obj.Objects:
            stack.extend((child, current_path) for child in obj.Objects)
        if hasattr(obj, "OutList") and obj.OutList:
            stack.extend((child, current_path) for child in obj.OutList)
        if hasattr(obj, "LinkedObject") and obj.LinkedObject:
            stack.append((obj.LinkedObject, current_path))

    return all_objects, materials, varsets, matrices


def calculate_bbox(shape):
    """Calculate box aligned with shape's placement"""
    import numpy as np

    if hasattr(shape, 'Placement'):
        shape_placement = shape.Placement
    else:
        parent = [obj for obj in FreeCAD.ActiveDocument.Objects if hasattr(obj, 'Shape') and obj.Shape == shape]
        if parent:
            shape_placement = parent[0].Placement
        else:
            shape_placement = FreeCAD.Placement()
    
    vertices = np.array([[v.Point.x, v.Point.y, v.Point.z] for v in shape.Vertexes])
    if len(vertices) < 4:
        return None
    
    # Calculate bounding box
    mins = np.min(vertices, axis=0)
    maxs = np.max(vertices, axis=0)
    dimensions = maxs - mins
    dimensions = np.maximum(dimensions, 0.001)
    center = np.mean(vertices, axis=0)
    
    # Use shape's rotation
    rot = shape_placement.Rotation.toMatrix()
    rot_matrix = np.array([
        [rot.A11, rot.A12, rot.A13],
        [rot.A21, rot.A22, rot.A23],
        [rot.A31, rot.A32, rot.A33]
    ])
    
    rotation = matrix_to_rotation(rot_matrix)
    result = {
        'dimensions': FreeCAD.Vector(dimensions[0], dimensions[1], dimensions[2]),
        'rotation': rotation,
        'center': FreeCAD.Vector(float(center[0]), float(center[1]), float(center[2]))
    }
    
    result['dimensions'] /= 1000.0
    result['center'].multiply(1/1000.0)

    return result


def matrix_to_rotation(matrix):
    """Convert rotation matrix to FreeCAD rotation"""
    import numpy as np
    
    x_axis = FreeCAD.Vector(float(matrix[0,0]), float(matrix[1,0]), float(matrix[2,0])).normalize()
    y_axis = FreeCAD.Vector(float(matrix[0,1]), float(matrix[1,1]), float(matrix[2,1])).normalize()
    z_axis = FreeCAD.Vector(float(matrix[0,2]), float(matrix[1,2]), float(matrix[2,2])).normalize()
    
    rot = FreeCAD.Matrix()
    rot.A11, rot.A12, rot.A13 = x_axis.x, y_axis.x, z_axis.x
    rot.A21, rot.A22, rot.A23 = x_axis.y, y_axis.y, z_axis.y
    rot.A31, rot.A32, rot.A33 = x_axis.z, y_axis.z, z_axis.z
    
    return FreeCAD.Rotation(rot)


def create_bbox_visualization(shape, bbox_info):
    """Create visual bounding box"""
    # Convert dimensions back to millimeters (they were converted to meters earlier)
    dimensions = [d * 1000.0 for d in bbox_info['dimensions']]
    rotation = bbox_info['rotation']
    
    bbox = FreeCAD.ActiveDocument.addObject("Part::Box", f"MinBBox")
    bbox.Length = float(dimensions[0])
    bbox.Width = float(dimensions[1])
    bbox.Height = float(dimensions[2])
    
    # Use shape's bounding box for initial positioning
    shape_bbox = shape.BoundBox
    
    shape_center = FreeCAD.Vector(
        (shape_bbox.XMin + shape_bbox.XMax) / 2,
        (shape_bbox.YMin + shape_bbox.YMax) / 2,
        (shape_bbox.ZMin + shape_bbox.ZMax) / 2
    )
    
    if hasattr(shape, 'Placement'):
        shape_placement = shape.Placement
    else:
        parent = [obj for obj in FreeCAD.ActiveDocument.Objects if hasattr(obj, 'Shape') and obj.Shape == shape]
        if parent:
            shape_placement = parent[0].Placement
        else:
            shape_placement = FreeCAD.Placement()
    
    box_center = FreeCAD.Vector(bbox.Length/2, bbox.Width/2, bbox.Height/2)
    
    final_position = shape_placement.multVec(shape_center) - rotation.multVec(box_center)
    
    bbox.Placement.Base = final_position - shape_placement.Base
    bbox.Placement.Rotation = rotation
    
    bbox.ViewObject.Transparency = 70
    bbox.ViewObject.ShapeColor = (0.0, 1.0, 0.0)


def resolve_link_chain(obj):
    if not hasattr(obj, "Placement"):
        return obj, None
    
    placement = obj.Placement
    base = obj
    visited = set()

    while hasattr(base, "LinkedObject") and base.LinkedObject:
        if base in visited:
            raise RuntimeError("Circular Link detected")
        visited.add(base)
        base = base.LinkedObject
        placement = placement.multiply(base.Placement)

    return base, placement


def placement_to_matrix(placement):
    rot = placement.Rotation.toMatrix()
    pos = placement.Base

    return [
        rot.A11, rot.A21, rot.A31, 0,
        rot.A12, rot.A22, rot.A32, 0,
        rot.A13, rot.A23, rot.A33, 0,
        pos.x,   pos.y,   pos.z,   1
    ]


def modify_gltf_data(gltf_data, materials, varsets, matrices):
    """GLTF data modification"""

    if not isinstance(gltf_data, dict):
        return gltf_data

    # Replace material names
    if "materials" in gltf_data and materials:
        for i, material in enumerate(gltf_data["materials"]):
            if i < len(materials) and isinstance(material, dict) and "name" in material:
                material["name"] = materials[i]

    # Inject VarSet data
    if "nodes" in gltf_data and varsets:
        for node in gltf_data["nodes"]:
            if isinstance(node, dict) and "name" in node and node["name"] in varsets:
                if "extras" not in node:
                    node["extras"] = {}
                node["extras"].update(varsets[node["name"]])

    # Inject matrix data
    if "nodes" in gltf_data and matrices:
        for node in gltf_data["nodes"]:
            if isinstance(node, dict) and "name" in node and node["name"] in matrices:
                if "extras" not in node:
                    node["extras"] = {}
                node["extras"].update({"world_to_body": matrices[node["name"]]})

    return gltf_data


def process_glb_file(filename, gltf_data):
    """GLB file processing"""
    with open(filename, "rb") as f:
        data = f.read()

    json_str = json.dumps(gltf_data, separators=(",", ":")).encode("utf-8")
    json_padding = (4 - (len(json_str) % 4)) % 4
    json_str += b" " * json_padding

    original_json_length = struct.unpack("<I", data[12:16])[0]
    binary_start = (
        20 + original_json_length
    )  # 12 (GLB header) + 8 (JSON chunk header) + JSON length
    binary_data = data[binary_start:]

    total_length = 20 + len(json_str) + len(binary_data)

    with open(filename, "wb") as f:
        f.write(b"glTF")  # glTF header
        f.write(struct.pack("<I", 2))  # Version
        f.write(struct.pack("<I", total_length))  # Total length
        f.write(struct.pack("<I", len(json_str)))  # JSON chunk length
        f.write(b"JSON")  # JSON chunk type
        f.write(json_str)
        if binary_data:
            f.write(binary_data)


def export(objects, filename):
    """GLTF/GLB export with proper material names and metadata"""

    _, materials, varsets, matrices = traverse_objects_once(objects)

    export_objects = [obj for obj in objects if hasattr(obj, "Shape") and obj.Shape]

    ImportGui.export(export_objects, filename)

    if not os.path.exists(filename):
        return

    if filename.lower().endswith(".gltf"):
        with open(filename, "r", encoding="utf-8") as f:
            gltf_data = json.load(f)

        gltf_data = modify_gltf_data(gltf_data, materials, varsets, matrices)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(gltf_data, f, indent=2)

    elif filename.lower().endswith(".glb"):
        with open(filename, "rb") as f:
            # Read GLB header
            magic = f.read(4)
            if magic != b"glTF":
                return

            struct.unpack("<I", f.read(4))[0]
            struct.unpack("<I", f.read(4))[0]
            json_length = struct.unpack("<I", f.read(4))[0]
            json_type = f.read(4)

            if json_type != b"JSON":
                return

            json_data = f.read(json_length)
            gltf_data = json.loads(json_data.decode("utf-8"))

        gltf_data = modify_gltf_data(gltf_data, materials, varsets, matrices)
        process_glb_file(filename, gltf_data)
