"""
Character Prefab Fixer Module
-----------------------------
1. Migrates legacy Unity/Synty 'GeneralSkeleton' paths to Godot 4's 'Skeleton3D'.
2. Configures character prefabs that inherit multi-character rigs (e.g. Characters.fbx)
   to hide all 17+ non-active meshes (visible = false) and activate only the target character.
"""

import os
import glob
import re
from typing import List


def fix_skeleton_paths(project_root: str) -> int:
    """
    Replaces all occurrences of GeneralSkeleton with Skeleton3D across all .tscn files.
    """
    updated_count = 0
    all_scenes = glob.glob(os.path.join(project_root, "**/*.tscn"), recursive=True)

    for scene_path in all_scenes:
        try:
            with open(scene_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if "GeneralSkeleton" in content:
                content = content.replace('parent="GeneralSkeleton"', 'parent="Skeleton3D"')
                content = content.replace('"GeneralSkeleton"', '"Skeleton3D"')
                content = content.replace("GeneralSkeleton/", "Skeleton3D/")

                with open(scene_path, "w", encoding="utf-8") as f:
                    f.write(content)
                updated_count += 1
        except Exception:
            pass

    return updated_count


def configure_character_prefab_visibility(project_root: str) -> int:
    """
    Scans character prefab files and ensures each prefab displays only its intended
    mesh under Skeleton3D while hiding all non-active variants.
    """
    updated_prefabs = 0

    # 1. CyberCity Characters
    cyber_prefabs = glob.glob(os.path.join(project_root, "**/PolygonCyberCity/Prefabs/Characters/*.prefab.tscn"), recursive=True)
    cyber_chars = [
        "SM_Chr_Robot_01", "SM_Chr_Robot_Damaged_01", "SM_Chr_Mercenary_Male_01",
        "SM_Chr_Street_Male_01", "SM_Chr_Racer_Female_01", "SM_Chr_Rich_Female_01",
        "SM_Chr_Rich_Male_01", "SM_Chr_Cyborg_Male_01", "SM_Chr_Cyborg_Female_01",
        "SM_Chr_Robot_Female_01", "SM_Chr_Helper_Bot_01", "SM_Chr_MurderKitten_01",
        "SM_Chr_Cat_01", "SM_Chr_PleasureBot_Male_01", "SM_Chr_Mercenary_Female_01",
        "SM_Chr_Male_Soldier_01", "SM_Chr_Female_Soldier_01", "SM_Chr_Robot_Mercenary_01"
    ]
    updated_prefabs += _apply_mesh_visibility(cyber_prefabs, cyber_chars)

    # 2. PolygonGeneric Characters
    generic_prefabs = glob.glob(os.path.join(project_root, "**/PolygonGeneric/Prefabs/Characters/*.prefab.tscn"), recursive=True)
    generic_chars = [
        "SM_Gen_Chr_Business_Female_01", "SM_Gen_Chr_Business_Male_01", "SM_Gen_Chr_Charred_01",
        "SM_Gen_Chr_Peasent_Female_01", "SM_Gen_Chr_Peasent_Male_01", "SM_Gen_Chr_Prisoner_Female_01",
        "SM_Gen_Chr_Prisoner_Male_01", "SM_Gen_Chr_Robot_01", "SM_Gen_Chr_Skeleton_01",
        "SM_Gen_Chr_Space_Male_01", "SM_Gen_Chr_Street_Female_01", "SM_Gen_Chr_Street_Female_02",
        "SM_Gen_Chr_Street_Female_03", "SM_Gen_Chr_Street_Female_04", "SM_Gen_Chr_Street_Male_01",
        "SM_Gen_Chr_Street_Male_02", "SM_Gen_Chr_Street_Male_03", "SM_Gen_Chr_Street_Male_04",
        "SM_Gen_Chr_Jumpsuit_Male_01", "SM_Gen_Chr_Jumpsuit_Female_01", "SM_Gen_Chr_Underwear_Male_01",
        "SM_Gen_Chr_Underwear_Female_01"
    ]
    updated_prefabs += _apply_mesh_visibility(generic_prefabs, generic_chars)

    return updated_prefabs


def _apply_mesh_visibility(prefab_paths: List[str], all_character_names: List[str]) -> int:
    count = 0
    for p in prefab_paths:
        base = os.path.splitext(os.path.basename(p))[0]
        if base.endswith(".prefab"):
            base = os.path.splitext(base)[0]

        target_char = base
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            modified = False
            for char_name in all_character_names:
                is_target = (char_name == target_char)
                pattern = rf'\[node name="{char_name}"[^\]]*parent="Skeleton3D"[^\]]*\]'
                m = re.search(pattern, content)
                if m:
                    node_start = m.start()
                    next_node = content.find("[node name=", m.end())
                    node_section = content[node_start:next_node] if next_node != -1 else content[node_start:]

                    new_section = node_section
                    if "visible =" in new_section:
                        new_section = re.sub(r"visible\s*=\s*(?:true|false)", f"visible = {str(is_target).lower()}", new_section)
                    else:
                        lines = new_section.splitlines()
                        lines.insert(1, f"visible = {str(is_target).lower()}")
                        new_section = "\n".join(lines)

                    if new_section != node_section:
                        content = content[:node_start] + new_section + (content[next_node:] if next_node != -1 else "")
                        modified = True

            if modified:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
                count += 1
        except Exception:
            pass

    return count
