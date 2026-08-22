"""
UID Synchronizer Module
-----------------------
1. Builds a project-wide path -> UID dictionary from .import and .tscn files.
2. Synchronizes all [ext_resource] UIDs across .tscn scenes and prefabs to eliminate
   all 'invalid UID: ... using text path instead' Godot 4 warnings.
"""

import os
import re
from typing import Dict, Tuple

IGNORED_DIRS = {".godot", ".git", "node_modules"}


def build_uid_database(project_root: str) -> Dict[str, str]:
    path_to_uid = {}

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            p = os.path.join(root, f)
            if f.endswith(".import"):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as fo:
                        txt = fo.read()
                    uid_m = re.search(r'uid="([^"]+)"', txt)
                    source_m = re.search(r'source_file="([^"]+)"', txt)
                    if uid_m and source_m:
                        path_to_uid[source_m.group(1)] = uid_m.group(1)
                except Exception:
                    pass
            elif f.endswith(".tscn"):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as fo:
                        first_line = fo.readline()
                    uid_m = re.search(r'uid="([^"]+)"', first_line)
                    if uid_m:
                        rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
                        path_to_uid[rel] = uid_m.group(1)
                except Exception:
                    pass

    return path_to_uid


def synchronize_scene_uids(project_root: str) -> Tuple[int, int]:
    path_to_uid = build_uid_database(project_root)
    all_scenes = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith(".tscn"):
                all_scenes.append(os.path.join(root, f))

    updated_scenes = 0
    uids_fixed = 0

    for scene_path in all_scenes:
        try:
            with open(scene_path, "r", encoding="utf-8", errors="ignore") as fo:
                content = fo.read()

            if "[ext_resource" not in content:
                continue

            lines = content.splitlines()
            modified = False
            new_lines = []

            for line in lines:
                if line.startswith("[ext_resource"):
                    path_m = re.search(r'path="([^"]+)"', line)
                    uid_m = re.search(r'uid="([^"]+)"', line)
                    if path_m and path_m.group(1) in path_to_uid:
                        correct_uid = path_to_uid[path_m.group(1)]
                        if uid_m:
                            if uid_m.group(1) != correct_uid:
                                line = line.replace(uid_m.group(1), correct_uid)
                                modified = True
                                uids_fixed += 1
                        else:
                            line = line.replace("[ext_resource ", f'[ext_resource uid="{correct_uid}" ')
                            modified = True
                            uids_fixed += 1
                new_lines.append(line)

            if modified:
                with open(scene_path, "w", encoding="utf-8") as fo:
                    fo.write("\n".join(new_lines) + "\n")
                updated_scenes += 1
        except Exception:
            pass

    return updated_scenes, uids_fixed
