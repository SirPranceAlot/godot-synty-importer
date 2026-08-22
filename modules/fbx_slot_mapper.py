"""
FBX Material Slot Mapper Module
-------------------------------
1. Scans binary FBX files to detect internal Maya/3ds Max material slot names.
2. Configures .fbx.import files with fbx/importer=0 (native ufbx) and attaches
   the post-import script.
3. Generates _subresources blocks mapping every detected slot to a valid .tres material.
"""

import os
import re
from typing import Dict, Set, Tuple

IGNORED_DIRS = {".godot", ".git", "node_modules"}


def find_pack_materials(pack_root: str, project_root: str) -> Dict[str, str]:
    """
    Scans a pack's directory to index available .mat.tres and .tres materials.
    Returns a dict of { material_stem_lower: res:// path }.
    """
    mat_map = {}
    for root, dirs, files in os.walk(pack_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith((".mat.tres", ".tres")) and not f.endswith(".mesh"):
                stem = f.replace(".mat.tres", "").replace(".tres", "").lower()
                rel_path = "res://" + os.path.relpath(os.path.join(root, f), project_root).replace("\\", "/")
                mat_map[stem] = rel_path
    return mat_map


def extract_fbx_material_slots(fbx_path: str) -> Set[str]:
    """
    Extracts all material slot names present in an FBX binary header.
    """
    slot_names = set()
    try:
        with open(fbx_path, "rb") as f:
            data = f.read()

        for m in re.findall(b"([a-zA-Z0-9_-]+)\x00\x01Material", data):
            slot_names.add(m.decode("ascii", errors="ignore"))

        for m in re.findall(b"Material::([a-zA-Z0-9_-]+)", data):
            slot_names.add(m.decode("ascii", errors="ignore"))
    except Exception:
        pass

    slot_names.add("default")
    slot_names.add("MAT_01A")
    slot_names.add("lambert1712")
    slot_names.add("roboguy_lambert4SG3")
    slot_names.add("pasted__lambert4SG3")

    return slot_names


def resolve_slot_material(
    slot_name: str,
    fbx_file_name: str,
    pack_name: str,
    available_materials: Dict[str, str],
    default_atlas_mat: str
) -> str:
    slot_lower = slot_name.lower()
    fname_lower = fbx_file_name.lower()

    if "glass" in slot_lower:
        for k, v in available_materials.items():
            if "glass" in k:
                return v

    if "holo" in slot_lower or "target" in slot_lower:
        for k, v in available_materials.items():
            if "holo" in k:
                return v

    if "posters" in slot_lower:
        for k, v in available_materials.items():
            if "poster" in k:
                return v
    if "signs" in slot_lower:
        for k, v in available_materials.items():
            if "sign" in k:
                return v
    if "trash" in slot_lower:
        for k, v in available_materials.items():
            if "trash" in k:
                return v

    if "road" in slot_lower or "tyre" in slot_lower:
        for k, v in available_materials.items():
            if "road" in k:
                return v
    if "water" in slot_lower:
        for k, v in available_materials.items():
            if "water" in k:
                return v
    if "rock" in slot_lower or "mountain" in slot_lower:
        for k, v in available_materials.items():
            if "rock" in k or "mountain" in k:
                return v

    if "sm_bld_block_" in fname_lower:
        for k, v in available_materials.items():
            if "parallax_full_01" in k or "parallax" in k:
                return v

    if "wall" in slot_lower or "a_wall" in slot_lower:
        num_match = re.search(r"(\d+)", slot_name)
        if num_match:
            target_key = f"wall_{num_match.group(1).zfill(2)}"
            if target_key in available_materials:
                return available_materials[target_key]
        for k, v in available_materials.items():
            if "wall_01_a" in k or "wall" in k:
                return v

    return default_atlas_mat


def map_pack_fbx_models(pack_dir: str, project_root: str) -> Tuple[int, int]:
    available_materials = find_pack_materials(pack_dir, project_root)
    pack_name = os.path.basename(pack_dir)

    default_atlas_mat = ""
    for k, v in available_materials.items():
        if "01_a" in k:
            default_atlas_mat = v
            break
    if not default_atlas_mat and available_materials:
        default_atlas_mat = next(iter(available_materials.values()))

    total_models = 0
    total_slots = 0
    post_import_script = "res://synty_post_import.gd"

    for root, dirs, files in os.walk(pack_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith(".fbx"):
                fbx_path = os.path.join(root, f)
                import_path = fbx_path + ".import"
                if not os.path.exists(import_path):
                    continue

                slot_names = extract_fbx_material_slots(fbx_path)
                material_lines = []

                for slot in sorted(slot_names):
                    target_mat = resolve_slot_material(slot, f, pack_name, available_materials, default_atlas_mat)
                    material_lines.append(
                        f'"{slot}": {{\n"use_external/enabled": true,\n"use_external/path": "{target_mat}"\n}}'
                    )
                    total_slots += 1

                materials_subresources = ',\n'.join(material_lines)
                subresources_block = f'_subresources={{\n"materials": {{\n{materials_subresources}\n}}\n}}'

                with open(import_path, "r", encoding="utf-8", errors="ignore") as fo:
                    content = fo.read()

                if "fbx/importer=1" in content:
                    content = content.replace("fbx/importer=1", "fbx/importer=0")
                elif "fbx/importer=" not in content:
                    p_idx = content.find("[params]")
                    if p_idx != -1:
                        content = content[:p_idx + 8] + "\nfbx/importer=0" + content[p_idx + 8:]

                if 'import_script/path=""' in content:
                    content = content.replace('import_script/path=""', f'import_script/path="{post_import_script}"')

                sub_start = content.find("_subresources=")
                if sub_start != -1:
                    in_sub = False
                    sub_end = sub_start
                    for idx in range(sub_start, len(content)):
                        if content[idx] == "{":
                            in_sub = True
                        elif content[idx] == "}" and in_sub:
                            sub_end = idx + 1
                            break
                    content = content[:sub_start] + subresources_block + content[sub_end:]
                else:
                    content += "\n" + subresources_block

                with open(import_path, "w", encoding="utf-8") as fo:
                    fo.write(content)

                total_models += 1

    return total_models, total_slots
