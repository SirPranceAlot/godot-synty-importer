"""
Character Prefab Fixer Module
-----------------------------
1. Migrates legacy Unity/Synty 'GeneralSkeleton' paths to Godot 4's 'Skeleton3D'.
2. Configures character prefabs that inherit multi-character rigs (e.g. Characters.fbx)
   to hide non-active meshes (visible = false) and activate only the target character.
"""

import os
import glob
import re
from typing import List


def fix_skeleton_paths(project_root: str) -> int:
    updated = 0
    for scene in glob.glob(os.path.join(project_root, "**/*.tscn"), recursive=True):
        try:
            with open(scene, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            if "GeneralSkeleton" in content:
                content = content.replace('parent="GeneralSkeleton"', 'parent="Skeleton3D"')
                content = content.replace('"GeneralSkeleton"', '"Skeleton3D"')
                content = content.replace("GeneralSkeleton/", "Skeleton3D/")
                with open(scene, "w", encoding="utf-8") as fh:
                    fh.write(content)
                updated += 1
        except Exception:
            pass
    return updated


def configure_character_prefab_visibility(project_root: str) -> int:
    updated = 0
    character_prefabs = glob.glob(os.path.join(project_root, "**/Prefabs/Characters/*.prefab.tscn"), recursive=True)

    for prefab_path in character_prefabs:
        stem = os.path.basename(prefab_path).split(".")[0]
        try:
            with open(prefab_path, "r", encoding="utf-8") as fh:
                content = fh.read()

            # Find all sibling character nodes parented to Skeleton3D
            char_nodes = re.findall(r'\[node name="([^"]+)"[^\]]*parent="Skeleton3D"[^\]]*\]', content)
            if len(char_nodes) <= 1:
                continue

            modified = False
            for char_name in char_nodes:
                is_target = (char_name == stem)
                m = re.search(rf'\[node name="{char_name}"[^\]]*parent="Skeleton3D"[^\]]*\]', content)
                if not m:
                    continue

                node_start = m.start()
                next_node = content.find("[node name=", m.end())
                section = content[node_start:next_node] if next_node != -1 else content[node_start:]

                if "visible =" in section:
                    new_sec = re.sub(r"visible\s*=\s*(?:true|false)", f"visible = {str(is_target).lower()}", section)
                else:
                    lines = section.splitlines()
                    lines.insert(1, f"visible = {str(is_target).lower()}")
                    new_sec = "\n".join(lines)

                if new_sec != section:
                    content = content[:node_start] + new_sec + (content[next_node:] if next_node != -1 else "")
                    modified = True

            if modified:
                with open(prefab_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                updated += 1
        except Exception:
            pass

    return updated
