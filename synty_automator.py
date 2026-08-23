#!/usr/bin/env python3
"""
Godot Synty Importer & Automator
================================
A comprehensive standalone tool and Godot 4 pipeline to automatically extract,
repair, map, configure, and optimize Synty asset packs in Godot 4.

Usage:
    python3 synty_automator.py [--path /path/to/godot_project] [--package /path/to/Pack.unitypackage] [--purge-cache]
"""

import argparse
import os
import sys
from modules.unitypackage_extractor import extract_unitypackage
from modules.texture_sanitizer import (
    sanitize_texture_formats,
    create_embedded_texture_aliases,
    normalize_texture_imports,
)
from modules.fbx_slot_mapper import map_pack_fbx_models
from modules.character_prefab_fixer import (
    fix_skeleton_paths,
    configure_character_prefab_visibility,
)
from modules.uid_synchronizer import synchronize_scene_uids


def run_pipeline(project_root: str, package_path: str = None, purge_cache: bool = False) -> None:
    project_root = os.path.abspath(project_root)
    print("==================================================", flush=True)
    print("       Godot 4 Synty Asset Automation Tool        ", flush=True)
    print("==================================================", flush=True)
    print(f"Target Project: {project_root}", flush=True)

    if not os.path.exists(os.path.join(project_root, "project.godot")):
        print(f"ERROR: No project.godot found at '{project_root}'!", flush=True)
        sys.exit(1)

    # Step 0: UnityPackage Extraction (if requested)
    if package_path:
        print(f"\n[0/4] Extracting UnityPackage: {os.path.basename(package_path)}...", flush=True)
        try:
            extracted_files, affected_packs = extract_unitypackage(package_path, project_root)
            print(f"      - Extracted {extracted_files} assets across {len(affected_packs)} pack(s).", flush=True)
        except Exception as e:
            print(f"ERROR: Failed to extract package: {e}", flush=True)
            sys.exit(1)

    # Step 1: Texture Sanitization & Repair
    print("\n[1/4] Sanitizing Texture Files & Embedded Aliases...", flush=True)
    fixed_tex = sanitize_texture_formats(project_root)
    created_aliases = create_embedded_texture_aliases(project_root)
    norm_tex = normalize_texture_imports(project_root)
    print(f"      - Fixed misnamed image formats: {fixed_tex}", flush=True)
    print(f"      - Created embedded texture aliases: {created_aliases}", flush=True)
    print(f"      - Normalized sRGB import settings: {norm_tex}", flush=True)

    # Step 2: FBX Binary Material Slot Mapping
    print("\n[2/4] Scanning & Mapping FBX Internal Material Slots...", flush=True)
    total_models = 0
    total_slots = 0
    synty_root = os.path.join(project_root, "Assets/Synty")
    if os.path.exists(synty_root):
        for pack in os.listdir(synty_root):
            pack_dir = os.path.join(synty_root, pack)
            if os.path.isdir(pack_dir):
                models, slots = map_pack_fbx_models(pack_dir, project_root)
                total_models += models
                total_slots += slots
    print(f"      - Mapped {total_slots} material slots across {total_models} FBX models.", flush=True)

    # Step 3: Character Rig & Prefab Migration
    print("\n[3/4] Rectifying Character Rigs & Mesh Visibility...", flush=True)
    fixed_skels = fix_skeleton_paths(project_root)
    fixed_chars = configure_character_prefab_visibility(project_root)
    print(f"      - Updated GeneralSkeleton -> Skeleton3D in {fixed_skels} scenes/prefabs.", flush=True)
    print(f"      - Applied selective mesh visibility to {fixed_chars} character prefabs.", flush=True)

    # Step 4: UID Database Synchronization
    print("\n[4/4] Synchronizing Scene & Resource UIDs...", flush=True)
    synced_scenes, fixed_uids = synchronize_scene_uids(project_root)
    print(f"      - Synchronized {fixed_uids} resource UIDs across {synced_scenes} scene files.", flush=True)

    # Optional: Cache Purge
    if purge_cache:
        print("\n[Optional] Purging Stale Compiled Binary Cache...", flush=True)
        imported_dir = os.path.join(project_root, ".godot/imported")
        purged = 0
        if os.path.exists(imported_dir):
            for f in os.listdir(imported_dir):
                if f.endswith(".scn") and any(k in f for k in ["SM_", "FX_", "Characters", "Generic_", "SK_"]):
                    try:
                        os.remove(os.path.join(imported_dir, f))
                        purged += 1
                    except Exception:
                        pass
        print(f"      - Purged {purged} cached .scn scene files.", flush=True)

    print("\n==================================================", flush=True)
    print("Automation completed successfully!", flush=True)
    print("Reload the project in Godot or restart the editor.", flush=True)
    print("==================================================", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Automates Synty asset configuration in Godot 4.")
    parser.add_argument(
        "--path",
        type=str,
        default=os.getcwd(),
        help="Path to the Godot project root (defaults to current directory).",
    )
    parser.add_argument(
        "--package", "-pkg",
        type=str,
        default=None,
        help="Optional path to a .unitypackage file to extract and import directly.",
    )
    parser.add_argument(
        "--purge-cache",
        action="store_true",
        help="Delete cached .scn files in .godot/imported (forces full re-import).",
    )
    args = parser.parse_args()

    run_pipeline(args.path, package_path=args.package, purge_cache=args.purge_cache)


if __name__ == "__main__":
    main()
