"""
FBX Material Slot Mapper Module
-------------------------------
1. Scans binary FBX files to detect internal Maya/3ds Max material slot names.
2. Configures .fbx.import files with fbx/importer=0 (native ufbx) and attaches
   the post-import script.
3. Generates _subresources blocks mapping every detected slot to a valid .tres material
   using nested brace depth tracking to prevent dangling parameters.
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


def clean_and_normalize_import_content(content: str, post_import_script: str) -> str:
    """
    Removes invalid flags, strips trailing/duplicate fbx parameters,
    and removes old _subresources blocks using exact brace-depth counting.
    """
    # 1. Remove valid=false
    content = content.replace("valid=false\n", "").replace("valid=false", "")

    # 2. Remove all fbx/ lines so we can place fbx/importer=0 cleanly in [params]
    content = re.sub(r"fbx/importer=\d+\n?", "", content)
    content = re.sub(r"fbx/allow_geometry_helper_nodes=.*\n?", "", content)
    content = re.sub(r"fbx/embedded_image_handling=.*\n?", "", content)
    content = re.sub(r"fbx/naming_version=.*\n?", "", content)

    # 3. Cleanly remove old _subresources block using proper brace depth
    sub_idx = content.find("_subresources=")
    if sub_idx != -1:
        brace_start = content.find("{", sub_idx)
        if brace_start != -1:
            depth = 0
            end_idx = len(content)
            for i in range(brace_start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            content = content[:sub_idx].rstrip() + "\n" + content[end_idx:].lstrip()

    # 4. Attach post-import script if missing
    if 'import_script/path=""' in content:
        content = content.replace('import_script/path=""', f'import_script/path="{post_import_script}"')

    # 5. Insert fbx/importer=0 under [params]
    params_idx = content.find("[params]")
    if params_idx != -1:
        content = content[:params_idx + 8] + "\nfbx/importer=0" + content[params_idx + 8:]

    return content.strip()


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

                materials_subresources = ",\n".join(material_lines)
                subresources_block = f'_subresources={{\n"materials": {{\n{materials_subresources}\n}}\n}}'

                with open(import_path, "r", encoding="utf-8", errors="ignore") as fo:
                    raw_content = fo.read()

                cleaned_base = clean_and_normalize_import_content(raw_content, post_import_script)
                final_content = cleaned_base + "\n\n" + subresources_block + "\n"

                with open(import_path, "w", encoding="utf-8") as fo:
                    fo.write(final_content)

                total_models += 1

    return total_models, total_slots
