"""
FBX Material Slot Mapper Module
-------------------------------
1. Extracts binary FBX material slots using optimized header regexes.
2. Resolves slots to appropriate .tres pack materials via a priority rule engine.
3. Configures .fbx.import files cleanly for Godot 4 native ufbx importer.
"""

import os
import re
from typing import Dict, Set, Tuple

IGNORED_DIRS = {".godot", ".git", "node_modules"}

FALLBACK_SLOTS = [
    "default", "Base_Lambert", "lambert", "lambert1", "standardSurface1",
    "MAT_01A", "MAT_01B", "COLOR", "Polygon", "Polygon_Generic_01A",
    "Scifi_Cybercity_Main", "custom_lambert", "pasted__lambert4SG3",
    "roboguy_lambert4SG3", "roboguy_lambert4SG11", "roboguy_lambert4SG12",
    "roboguy_lambert4SG13", "roboguy_lambert4SG14", "roboguy_lambert4SG15",
    "roboguy_lambert4SG16", "roboguy_lambert4SG17", "roboguy_lambert4SG18",
    "roboguy_lambert4SG19", "roboguy_lambert4SG6"
]

# Declarative resolution rules: (predicate(slot_lower, file_lower), candidate_material_keys)
SLOT_RULES = [
    (lambda s, f: "target_hologram" in f or "holotarget" in s or ("target" in s and "holo" in f), ["hologram_targets_01", "hologram_targets", "hologram_01"]),
    (lambda s, f: "holo_sign" in f or "holosign" in s, ["hologram_signs_01", "hologram_signs", "hologram_01"]),
    (lambda s, f: "holo_poster" in f, ["hologram_posters_01_a", "hologram_posters_01_b", "hologram_01"]),
    (lambda s, f: "holo_text" in f, ["hologram_text_01", "hologram_01"]),
    (lambda s, f: "hologram_tree" in f or "hologram_cherry_tree" in f, ["hologram_01", "hologram_basic_01_a"]),
    (lambda s, f: ("hologram_stand" in f or "hologram_table" in f or "holo_planter" in f) and ("holo" in s or "screen" in s), ["hologram_01", "hologram_basic_01_a"]),
    (lambda s, f: "holo" in s or "hologram" in s, ["hologram_01", "hologram_basic_01_a"]),
    (lambda s, f: "poster" in f or "poster" in s or "papers" in s, ["posters_01", "poster_01", "papers_01"]),
    (lambda s, f: "damaged_sign" in f or "billboard_damaged" in f, ["billboard_01_damaged", "billboard_02_damaged", "billboard_01_a"]),
    (lambda s, f: "billboard_sign_small" in f or "billboard_backing_small" in f, ["billboard_03", "billboard_01_a"]),
    (lambda s, f: "billboard" in f or "billboard" in s, ["billboard_01_a", "billboard_02_a", "billboard_03"]),
    (lambda s, f: "neonsign" in f or "sign" in f or "sign" in s, ["signs_01", "billboard_03", "billboard_01_a"]),
    (lambda s, f: "glass" in s or "glass" in f, ["glass_01_a", "glass_transparent_01", "glass_01", "glass"]),
    (lambda s, f: "trash" in s or "trash" in f, ["trash_01", "junk_01"]),
    (lambda s, f: "junk_large" in f, ["junk_large_01", "junk_01"]),
    (lambda s, f: "junk" in f, ["junk_01"]),
    (lambda s, f: "laser_grid" in f, ["laser_grid_01", "laser_01"]),
    (lambda s, f: "laser" in f, ["laser_01"]),
    (lambda s, f: "fx_leaf" in f or "fx_leaves" in f, ["fx_leaves_01", "fx_leaves_02", "fx_leaves_03"]),
    (lambda s, f: "fx_lightray" in f, ["fx_lightray_01", "fx_lightray_02"]),
    (lambda s, f: "fx_fish" in f, ["fx_fish_pixel_01"]),
    (lambda s, f: "fx_gradient" in f, ["fx_gradient_01"]),
    (lambda s, f: "sm_bld_block_" in f or "parallax" in f or "parallax" in s, ["parallax_full_01", "parallax_01", "parallax"]),
]


def find_pack_materials(pack_root: str, project_root: str) -> Dict[str, str]:
    mat_map = {}
    for root, dirs, files in os.walk(pack_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith((".mat.tres", ".tres")) and not f.endswith(".mesh"):
                stem = f.replace(".mat.tres", "").replace(".tres", "").lower()
                rel = "res://" + os.path.relpath(os.path.join(root, f), project_root).replace("\\", "/")
                mat_map[stem] = rel
    return mat_map


def extract_fbx_material_slots(fbx_path: str) -> Set[str]:
    slots = set()
    try:
        with open(fbx_path, "rb") as fh:
            data = fh.read(512 * 1024)
        for m in re.findall(b"([a-zA-Z0-9_-]+)\x00\x01Material", data):
            slots.add(m.decode("ascii", errors="ignore"))
        for m in re.findall(b"Material::([a-zA-Z0-9_-]+)", data):
            slots.add(m.decode("ascii", errors="ignore"))
        for m in re.findall(b"Material[\x00-\x10]+([a-zA-Z0-9_ -]{2,40})[\x00-\x10]+(?:FbxSurfaceLambert|FbxSurfacePhong|Material)", data):
            slots.add(m.decode("ascii", errors="ignore").strip())
        for m in re.findall(b"([a-zA-Z0-9_ -]{2,40})\x00\x01(?:FbxSurfaceLambert|FbxSurfacePhong)", data):
            slots.add(m.decode("ascii", errors="ignore").strip())
        for m in re.findall(b"Material\x00+([a-zA-Z0-9_-]+)", data):
            slots.add(m.decode("ascii", errors="ignore"))
    except Exception:
        pass

    slots.update(FALLBACK_SLOTS)
    return {s for s in slots if len(s) >= 2 and not s.startswith(" ")}


def resolve_slot_material(slot_name: str, fbx_file_name: str, available_materials: Dict[str, str], default_atlas_mat: str) -> str:
    s_low = slot_name.lower()
    f_low = fbx_file_name.lower().replace(".fbx", "")

    # 1. Evaluate rule engine
    for predicate, candidates in SLOT_RULES:
        if predicate(s_low, f_low):
            for cand in candidates:
                if cand in available_materials:
                    return available_materials[cand]

    # 2. Wall / Brick / Floor numbered matching
    if any(k in s_low for k in ["wall", "a_wall", "brick", "stucco", "floor"]):
        num_m = re.search(r"(\d+)", slot_name)
        if num_m:
            target_key = f"wall_{num_m.group(1).zfill(2)}"
            for k in [f"{target_key}_a", f"{target_key}_b", target_key]:
                if k in available_materials:
                    return available_materials[k]
        for k, v in available_materials.items():
            if any(term in k for term in ["wall_01_a", "wall", "brick", "floor"]):
                return v

    # 3. Nature elements
    if any(k in s_low for k in ["tree", "rock", "mountain", "water"]):
        for k, v in available_materials.items():
            if any(term in k for term in ["tree", "rock", "mountain", "water", "nature"]):
                return v

    # 4. Suffix alts (e.g. _01_B, _02_A)
    for sfx in ["_01_b", "_01_c", "_02_a", "_02_b", "_02_c", "_03_a", "_03_b", "_03_c", "_04_a", "_04_b", "_04_c"]:
        if f_low.endswith(sfx):
            for k, v in available_materials.items():
                if sfx[1:] in k:
                    return v

    return default_atlas_mat


def clean_and_normalize_import(content: str) -> str:
    content = content.replace("valid=false\n", "").replace("valid=false", "")
    content = re.sub(r"fbx/importer=\d+\n?", "", content)
    content = re.sub(r"fbx/allow_geometry_helper_nodes=.*\n?", "", content)
    content = re.sub(r"fbx/embedded_image_handling=.*\n?", "", content)
    content = re.sub(r"fbx/naming_version=.*\n?", "", content)

    # Strip existing _subresources block with brace tracking
    sub_idx = content.find("_subresources=")
    if sub_idx != -1:
        brace_start = content.find("{", sub_idx)
        if brace_start != -1:
            depth, end_idx = 0, len(content)
            for i in range(brace_start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            content = content[:sub_idx].rstrip() + "\n" + content[end_idx:].lstrip()

    content = re.sub(r'import_script/path="[^"]*"', 'import_script/path=""', content)
    params_idx = content.find("[params]")
    if params_idx != -1:
        content = content[:params_idx + 8] + "\nfbx/importer=0\nfbx/embedded_image_handling=0" + content[params_idx + 8:]

    return content.strip()


def map_pack_fbx_models(pack_dir: str, project_root: str) -> Tuple[int, int]:
    available_mats = find_pack_materials(pack_dir, project_root)
    default_atlas = next((v for k, v in available_mats.items() if "01_a" in k or "colormap" in k), next(iter(available_mats.values()), ""))

    total_models = total_slots = 0
    for root, dirs, files in os.walk(pack_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith(".fbx"):
                imp_path = os.path.join(root, f) + ".import"
                if not os.path.exists(imp_path):
                    continue

                slots = extract_fbx_material_slots(os.path.join(root, f))
                mat_lines = [
                    f'"{s}": {{\n"use_external/enabled": true,\n"use_external/path": "{resolve_slot_material(s, f, available_mats, default_atlas)}"\n}}'
                    for s in sorted(slots)
                ]
                total_slots += len(mat_lines)

                with open(imp_path, "r", encoding="utf-8", errors="ignore") as fh:
                    raw = fh.read()

                sub_body = ",\n".join(mat_lines)
                final_txt = clean_and_normalize_import(raw) + f'\n\n_subresources={{\n"materials": {{\n{sub_body}\n}}\n}}\n'
                with open(imp_path, "w", encoding="utf-8") as fh:
                    fh.write(final_txt)
                total_models += 1

    return total_models, total_slots
