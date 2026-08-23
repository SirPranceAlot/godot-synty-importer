"""
UID Synchronizer Module
-----------------------
1. Builds a project-wide path -> UID dictionary from .import and .tscn files.
2. Synchronizes [ext_resource] UIDs across .tscn scenes and prefabs to eliminate
   'invalid UID: ... using text path instead' Godot 4 warnings.
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
            path = os.path.join(root, f)
            try:
                if f.endswith(".import"):
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        txt = fh.read()
                    uid_m = re.search(r'uid="([^"]+)"', txt)
                    src_m = re.search(r'source_file="([^"]+)"', txt)
                    if uid_m and src_m:
                        path_to_uid[src_m.group(1)] = uid_m.group(1)
                elif f.endswith(".tscn"):
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        first_line = fh.readline()
                    uid_m = re.search(r'uid="([^"]+)"', first_line)
                    if uid_m:
                        rel = "res://" + os.path.relpath(path, project_root).replace("\\", "/")
                        path_to_uid[rel] = uid_m.group(1)
            except Exception:
                pass
    return path_to_uid


def synchronize_scene_uids(project_root: str) -> Tuple[int, int]:
    path_to_uid = build_uid_database(project_root)
    updated_scenes = uids_fixed = 0

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if not f.endswith(".tscn"):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                if "[ext_resource" not in content:
                    continue

                lines = content.splitlines()
                modified = False
                new_lines = []

                for line in lines:
                    if line.startswith("[ext_resource"):
                        p_m = re.search(r'path="([^"]+)"', line)
                        u_m = re.search(r'uid="([^"]+)"', line)
                        if p_m and p_m.group(1) in path_to_uid:
                            target_uid = path_to_uid[p_m.group(1)]
                            if u_m:
                                if u_m.group(1) != target_uid:
                                    line = line.replace(u_m.group(1), target_uid)
                                    modified = True
                                    uids_fixed += 1
                            else:
                                line = line.replace("[ext_resource ", f'[ext_resource uid="{target_uid}" ')
                                modified = True
                                uids_fixed += 1
                    new_lines.append(line)

                if modified:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(new_lines) + "\n")
                    updated_scenes += 1
            except Exception:
                pass

    return updated_scenes, uids_fixed
