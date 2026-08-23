#!/usr/bin/env python3
"""
Godot Synty Importer & Automator (Unified Single-File Tool)
==========================================================
Automates extraction, texture repair, Maya/Max material slot mapping,
multi-character rig visibility, and UID synchronization for Synty asset packs in Godot 4.

Usage:
    python3 synty_automator.py [--path /path/to/godot_project] [--package /path/to/pack.unitypackage] [--purge-cache]
"""

import argparse
import os
import re
import sys
import tarfile
from typing import Dict, List, Set, Tuple
from PIL import Image

IGNORED_DIRS = {".godot", ".git", "node_modules", ".import"}

FALLBACK_SLOTS = [
    "default", "Base_Lambert", "lambert", "lambert1", "standardSurface1",
    "MAT_01A", "MAT_01B", "COLOR", "Polygon", "Polygon_Generic_01A",
    "Scifi_Cybercity_Main", "custom_lambert", "pasted__lambert4SG3",
    "roboguy_lambert4SG3", "roboguy_lambert4SG11", "roboguy_lambert4SG12",
    "roboguy_lambert4SG13", "roboguy_lambert4SG14", "roboguy_lambert4SG15",
    "roboguy_lambert4SG16", "roboguy_lambert4SG17", "roboguy_lambert4SG18",
    "roboguy_lambert4SG19", "roboguy_lambert4SG6"
]

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


# ==============================================================================
# 1. UnityPackage Extractor
# ==============================================================================
def extract_unitypackage(package_path: str, destination_root: str) -> int:
    if not os.path.exists(package_path):
        raise FileNotFoundError(f"Package not found: {package_path}")

    extracted = 0
    with tarfile.open(package_path, "r:*") as tar:
        entries = {}
        for m in tar.getmembers():
            parts = m.name.replace("\\", "/").split("/")
            if len(parts) >= 2:
                entries.setdefault(parts[0], {})[parts[1]] = m

        for guid, items in entries.items():
            if "pathname" in items and "asset" in items:
                try:
                    pf = tar.extractfile(items["pathname"])
                    if not pf:
                        continue
                    rel = pf.read().decode("utf-8", errors="ignore").splitlines()[0].strip()
                    if not rel:
                        continue
                    target = os.path.join(destination_root, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    af = tar.extractfile(items["asset"])
                    if af:
                        with open(target, "wb") as out:
                            out.write(af.read())
                        extracted += 1
                except Exception:
                    pass
    return extracted


# ==============================================================================
# 2. Texture Sanitizer & Import Normalizer
# ==============================================================================
def save_image_safe(img: Image.Image, target_path: str) -> None:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    ext = os.path.splitext(target_path)[1].lower()
    img.save(target_path, format="PNG" if ext == ".png" else ("TGA" if ext == ".tga" else "JPEG"))


def sanitize_textures_and_stubs(project_root: str, all_files: List[str]) -> Tuple[int, int, int]:
    fixed = aliases = norm = 0
    ext_to_fmt = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}

    # Find color atlases for cloning
    atlases = {}
    for p in all_files:
        low = os.path.basename(p).lower()
        if "colormap" in low and "colormap_dst" not in low:
            atlases["colormap"] = p
        elif low == "polygoncybercity_texture_01_a.png":
            atlases["cyber"] = p
        elif low == "polygongeneric_texture_01_a.png":
            atlases["generic"] = p

    default_atlas = atlases.get("cyber") or atlases.get("generic") or next(iter(atlases.values()), None)

    # 1. Format check and .psd purge
    for p in all_files:
        ext = os.path.splitext(p)[1].lower()
        if ext == ".psd" or p.endswith(".psd.import"):
            try:
                os.remove(p)
            except Exception:
                pass
        elif ext in ext_to_fmt or ext == ".tga":
            try:
                with open(p, "rb") as fh:
                    hdr = fh.read(16)
                mismatch = False
                if ext in ext_to_fmt and not hdr.startswith(ext_to_fmt[ext]):
                    mismatch = True
                elif ext == ".tga" and (hdr.startswith(b"\x89PNG") or hdr.startswith(b"\xff\xd8")):
                    mismatch = True

                if mismatch:
                    with Image.open(p) as img:
                        save_image_safe(img, p)
                        fixed += 1
            except Exception:
                pass
        elif p.endswith((".png.import", ".tga.import", ".jpg.import", ".webp.import")) and "normal" not in p.lower():
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
                mod = False
                if "valid=false" in txt:
                    txt = txt.replace("valid=false\n", "").replace("valid=false", "")
                    mod = True
                if "compress/normal_map=2" in txt or "compress/normal_map=1" in txt:
                    txt = re.sub(r"compress/normal_map=\d+", "compress/normal_map=0", txt)
                    mod = True
                if "roughness/mode=1" in txt or "roughness/mode=2" in txt:
                    txt = re.sub(r"roughness/mode=\d+", "roughness/mode=0", txt)
                    mod = True
                if mod:
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(txt)
                    norm += 1
            except Exception:
                pass

    # 2. Required legacy texture aliases
    stubs = [
        "Assets/PolygonApocalypse/Textures/PolygonApocalypse_Texture_01_A 1.png",
        "Assets/PolygonApocalypse/Textures/Misc/PolygonApocalypse_Emissive_01.png",
        "Assets/PolygonApocalypse/Textures/Misc/PolygonApocalypse_Normal.png",
        "Assets/AnimationBaseLocomotion/Samples/Meshes/SimpleSky.png",
        "Assets/Synty/_SidekickCharacters/_published/_textures/Base_ColorLabels_01.png",
        "Assets/Synty/_SidekickCharacters/_Textures/_Working/Base_Color_01.png",
        "Assets/Synty/_Working/Jordan/_textures/sci_fi_sold_01.png",
        "Assets/Synty/SidekickCharacters/Resources/Meshes/Outfits/ScifiSoldiers/Sci-fiSoldier_Color_01_Label.png",
        "Assets/Synty/SidekickCharacters/Resources/Meshes/Outfits/ScifiSoldiers/sci_fi_sold_01.png",
        "Assets/Synty/Dropbox/SyntyStudios_CharacterDesigner/_Working/Jordan/_textures/sci_fi_sold_01.png",
        "Dropbox/SyntyStudios_CharacterDesigner/_Working/Jordan/_textures/sci_fi_sold_01.png",
        "Assets/Synty/Dropbox/SyntyStudios_CharacterDesigner/_SidekickCharacters/_published/_textures/Base_ColorLabels_01.png",
        "Dropbox/SyntyStudios_CharacterDesigner/_SidekickCharacters/_published/_textures/Base_ColorLabels_01.png",
        "Assets/Synty/Dropbox/SyntyStudios_CharacterDesigner/_SidekickCharacters/_Textures/_Working/Base_Color_01.png",
        "Dropbox/SyntyStudios_CharacterDesigner/_SidekickCharacters/_Textures/_Working/Base_Color_01.png",
    ]
    for rel in stubs:
        target = os.path.join(project_root, rel)
        if not os.path.exists(target):
            src = atlases.get("colormap") if ("Sidekick" in rel or "Character" in rel) else default_atlas
            try:
                if src and os.path.exists(src):
                    with Image.open(src) as img:
                        save_image_safe(img, target)
                else:
                    save_image_safe(Image.new("RGBA", (256, 256), (200, 200, 200, 255)), target)
                aliases += 1
            except Exception:
                pass

    return fixed, aliases, norm


# ==============================================================================
# 3. FBX Material Slot Mapper
# ==============================================================================
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


def resolve_slot_material(slot: str, fbx_name: str, mats: Dict[str, str], default_atlas: str) -> str:
    s_low, f_low = slot.lower(), fbx_name.lower().replace(".fbx", "")

    for predicate, candidates in SLOT_RULES:
        if predicate(s_low, f_low):
            for cand in candidates:
                if cand in mats:
                    return mats[cand]

    if any(k in s_low for k in ["wall", "a_wall", "brick", "stucco", "floor"]):
        num_m = re.search(r"(\d+)", slot)
        if num_m:
            target_key = f"wall_{num_m.group(1).zfill(2)}"
            for k in [f"{target_key}_a", f"{target_key}_b", target_key]:
                if k in mats:
                    return mats[k]
        for k, v in mats.items():
            if any(term in k for term in ["wall_01_a", "wall", "brick", "floor"]):
                return v

    if any(k in s_low for k in ["tree", "rock", "mountain", "water"]):
        for k, v in mats.items():
            if any(term in k for term in ["tree", "rock", "mountain", "water", "nature"]):
                return v

    for sfx in ["_01_b", "_01_c", "_02_a", "_02_b", "_02_c", "_03_a", "_03_b", "_03_c", "_04_a", "_04_b", "_04_c"]:
        if f_low.endswith(sfx):
            for k, v in mats.items():
                if sfx[1:] in k:
                    return v

    return default_atlas


def clean_import_file(content: str) -> str:
    content = content.replace("valid=false\n", "").replace("valid=false", "")
    content = re.sub(r"fbx/importer=\d+\n?", "", content)
    content = re.sub(r"fbx/allow_geometry_helper_nodes=.*\n?", "", content)
    content = re.sub(r"fbx/embedded_image_handling=.*\n?", "", content)
    content = re.sub(r"fbx/naming_version=.*\n?", "", content)

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


def map_all_fbx_materials(project_root: str, all_files: List[str]) -> Tuple[int, int]:
    # Index all materials per pack folder
    pack_materials: Dict[str, Dict[str, str]] = {}
    for p in all_files:
        if p.endswith((".mat.tres", ".tres")) and not p.endswith(".mesh"):
            stem = os.path.basename(p).replace(".mat.tres", "").replace(".tres", "").lower()
            rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
            pack_dir = os.path.dirname(os.path.dirname(p))
            pack_materials.setdefault(pack_dir, {})[stem] = rel

    total_models = total_slots = 0
    for p in all_files:
        if p.endswith(".fbx"):
            imp_path = p + ".import"
            if not os.path.exists(imp_path):
                continue

            pack_dir = os.path.dirname(os.path.dirname(p))
            mats = pack_materials.get(pack_dir, {})
            default_atlas = next((v for k, v in mats.items() if "01_a" in k or "colormap" in k), next(iter(mats.values()), ""))

            slots = extract_fbx_material_slots(p)
            fbx_name = os.path.basename(p)
            mat_lines = [
                f'"{s}": {{\n"use_external/enabled": true,\n"use_external/path": "{resolve_slot_material(s, fbx_name, mats, default_atlas)}"\n}}'
                for s in sorted(slots)
            ]
            total_slots += len(mat_lines)

            try:
                with open(imp_path, "r", encoding="utf-8", errors="ignore") as fh:
                    raw = fh.read()
                sub_body = ",\n".join(mat_lines)
                final_txt = clean_import_file(raw) + f'\n\n_subresources={{\n"materials": {{\n{sub_body}\n}}\n}}\n'
                with open(imp_path, "w", encoding="utf-8") as fh:
                    fh.write(final_txt)
                total_models += 1
            except Exception:
                pass

    return total_models, total_slots


# ==============================================================================
# 4. Character Rig Migration & Visibility Configuration
# ==============================================================================
def fix_character_rigs_and_visibility(all_files: List[str]) -> Tuple[int, int]:
    fixed_skels = fixed_prefabs = 0

    # 1. Skeleton3D replacement
    for p in all_files:
        if p.endswith(".tscn"):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
                if "GeneralSkeleton" in txt:
                    txt = txt.replace('parent="GeneralSkeleton"', 'parent="Skeleton3D"')
                    txt = txt.replace('"GeneralSkeleton"', '"Skeleton3D"')
                    txt = txt.replace("GeneralSkeleton/", "Skeleton3D/")
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(txt)
                    fixed_skels += 1
            except Exception:
                pass

    # 2. Multi-character prefab visibility
    for p in all_files:
        if p.endswith(".prefab.tscn") and "/Characters/" in p.replace("\\", "/"):
            stem = os.path.basename(p).split(".")[0]
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    txt = fh.read()
                char_nodes = re.findall(r'\[node name="([^"]+)"[^\]]*parent="Skeleton3D"[^\]]*\]', txt)
                if len(char_nodes) <= 1:
                    continue

                modified = False
                for cname in char_nodes:
                    is_target = (cname == stem)
                    m = re.search(rf'\[node name="{cname}"[^\]]*parent="Skeleton3D"[^\]]*\]', txt)
                    if not m:
                        continue
                    n_start = m.start()
                    next_n = txt.find("[node name=", m.end())
                    sec = txt[n_start:next_n] if next_n != -1 else txt[n_start:]

                    if "visible =" in sec:
                        new_sec = re.sub(r"visible\s*=\s*(?:true|false)", f"visible = {str(is_target).lower()}", sec)
                    else:
                        lines = sec.splitlines()
                        lines.insert(1, f"visible = {str(is_target).lower()}")
                        new_sec = "\n".join(lines)

                    if new_sec != sec:
                        txt = txt[:n_start] + new_sec + (txt[next_n:] if next_n != -1 else "")
                        modified = True

                if modified:
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(txt)
                    fixed_prefabs += 1
            except Exception:
                pass

    return fixed_skels, fixed_prefabs


# ==============================================================================
# 5. Scene & Resource UID Synchronizer
# ==============================================================================
def synchronize_uids(project_root: str, all_files: List[str]) -> Tuple[int, int]:
    path_to_uid = {}
    for p in all_files:
        try:
            if p.endswith(".import"):
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
                uid_m = re.search(r'uid="([^"]+)"', txt)
                src_m = re.search(r'source_file="([^"]+)"', txt)
                if uid_m and src_m:
                    path_to_uid[src_m.group(1)] = uid_m.group(1)
            elif p.endswith(".tscn"):
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    fline = fh.readline()
                uid_m = re.search(r'uid="([^"]+)"', fline)
                if uid_m:
                    rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
                    path_to_uid[rel] = uid_m.group(1)
        except Exception:
            pass

    updated_scenes = uids_fixed = 0
    for p in all_files:
        if not p.endswith(".tscn"):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            if "[ext_resource" not in txt:
                continue

            lines = txt.splitlines()
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
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(new_lines) + "\n")
                updated_scenes += 1
        except Exception:
            pass

    return updated_scenes, uids_fixed


# ==============================================================================
# Pipeline Coordinator
# ==============================================================================
def collect_project_files(project_root: str) -> List[str]:
    files_list = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            files_list.append(os.path.join(root, f))
    return files_list


def run_pipeline(project_root: str, package_path: str = None, purge_cache: bool = False) -> None:
    project_root = os.path.abspath(project_root)
    print("==================================================", flush=True)
    print("       Godot 4 Synty Asset Automation Tool        ", flush=True)
    print("==================================================", flush=True)
    print(f"Target Project: {project_root}", flush=True)

    if not os.path.exists(os.path.join(project_root, "project.godot")):
        print(f"ERROR: No project.godot found at '{project_root}'!", flush=True)
        sys.exit(1)

    if package_path:
        print(f"\n[0/4] Extracting UnityPackage: {os.path.basename(package_path)}...", flush=True)
        extracted = extract_unitypackage(package_path, project_root)
        print(f"      - Extracted {extracted} files into project.", flush=True)

    # Unified single-pass filesystem scan
    all_files = collect_project_files(project_root)

    print("\n[1/4] Sanitizing Texture Files & Embedded Aliases...", flush=True)
    fixed_tex, aliases, norm_tex = sanitize_textures_and_stubs(project_root, all_files)
    print(f"      - Fixed misnamed image formats: {fixed_tex}", flush=True)
    print(f"      - Created embedded texture aliases: {aliases}", flush=True)
    print(f"      - Normalized sRGB import settings: {norm_tex}", flush=True)

    print("\n[2/4] Scanning & Mapping FBX Internal Material Slots...", flush=True)
    models, slots = map_all_fbx_materials(project_root, all_files)
    print(f"      - Mapped {slots} material slots across {models} FBX models.", flush=True)

    print("\n[3/4] Rectifying Character Rigs & Mesh Visibility...", flush=True)
    fixed_skels, fixed_chars = fix_character_rigs_and_visibility(all_files)
    print(f"      - Updated GeneralSkeleton -> Skeleton3D in {fixed_skels} scenes/prefabs.", flush=True)
    print(f"      - Applied selective mesh visibility to {fixed_chars} character prefabs.", flush=True)

    print("\n[4/4] Synchronizing Scene & Resource UIDs...", flush=True)
    synced_scenes, fixed_uids = synchronize_uids(project_root, all_files)
    print(f"      - Synchronized {fixed_uids} resource UIDs across {synced_scenes} scene files.", flush=True)

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
    parser.add_argument("--path", type=str, default=os.getcwd(), help="Path to Godot project root.")
    parser.add_argument("--package", "-pkg", type=str, default=None, help="Path to .unitypackage file to extract and import.")
    parser.add_argument("--purge-cache", action="store_true", help="Delete cached .scn files in .godot/imported.")
    args = parser.parse_args()
    run_pipeline(args.path, package_path=args.package, purge_cache=args.purge_cache)


if __name__ == "__main__":
    main()
