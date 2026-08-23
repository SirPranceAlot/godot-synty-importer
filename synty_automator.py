#!/usr/bin/env python3
"""
Godot Synty Importer & Automator (Universal Parallel Engine)
============================================================
Autonomous, universal importer and configuration engine for all Synty Studios
3D asset packs in Godot 4.

Handles:
  - Raw .unitypackage extraction
  - Dynamic missing texture & PSD alias resolution
  - Automated StandardMaterial3D generation for packs lacking Godot materials
  - Deep FBX binary material slot parsing & 4-tier semantic material mapping
  - Universal multi-character rig hierarchy & selective mesh visibility
  - Project-wide scene & resource UID synchronization
  - World-triplanar UV & normal map import normalization

Usage:
    python3 synty_automator.py [--path /path/to/godot_project] [--package /path/to/pack.unitypackage] [--purge-cache]
"""

import argparse
import os
import re
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IGNORED_DIRS = {".godot", ".git", "node_modules", ".import"}
MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)

# Pre-allocated 1x1 transparent PNG fallback bytes
PNG_1X1_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
    b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Universal fallback material slots found across Maya/3ds Max Synty exports
FALLBACK_SLOTS = [
    "default", "Base_Lambert", "lambert", "lambert1", "standardSurface1",
    "MAT_01A", "MAT_01B", "COLOR", "Polygon", "Polygon_Generic_01A",
    "Scifi_Cybercity_Main", "custom_lambert", "pasted__lambert4SG3",
    "roboguy_lambert4SG3", "roboguy_lambert4SG11", "roboguy_lambert4SG12",
    "roboguy_lambert4SG13", "roboguy_lambert4SG14", "roboguy_lambert4SG15",
    "roboguy_lambert4SG16", "roboguy_lambert4SG17", "roboguy_lambert4SG18",
    "roboguy_lambert4SG19", "roboguy_lambert4SG6"
]

# Universal Semantic Tag-to-Candidate rules
SEMANTIC_RULES = [
    # Holograms, Signs & Billboards
    ("target_hologram", ["hologram_targets_01", "hologram_targets", "hologram_01"]),
    ("holotarget", ["hologram_targets_01", "hologram_01"]),
    ("holo_sign", ["hologram_signs_01", "hologram_signs", "hologram_01"]),
    ("holosign", ["hologram_signs_01", "hologram_signs", "hologram_01"]),
    ("holo_poster", ["hologram_posters_01_a", "hologram_posters_01_b", "hologram_01"]),
    ("holo_text", ["hologram_text_01", "hologram_01"]),
    ("hologram_tree", ["hologram_01", "hologram_basic_01_a"]),
    ("hologram_cherry_tree", ["hologram_01", "hologram_basic_01_a"]),
    ("hologram", ["hologram_01", "hologram_basic_01_a"]),
    ("holo", ["hologram_01", "hologram_basic_01_a"]),
    ("poster", ["posters_01", "poster_01", "papers_01"]),
    ("damaged_sign", ["billboard_01_damaged", "billboard_02_damaged", "billboard_01_a"]),
    ("billboard_damaged", ["billboard_01_damaged", "billboard_02_damaged", "billboard_01_a"]),
    ("billboard_sign_small", ["billboard_03", "billboard_01_a"]),
    ("billboard_backing_small", ["billboard_03", "billboard_01_a"]),
    ("billboard", ["billboard_01_a", "billboard_02_a", "billboard_03"]),
    ("neonsign", ["signs_01", "billboard_03", "billboard_01_a"]),
    ("sign", ["signs_01", "billboard_03", "billboard_01_a"]),
    ("screen", ["screen_01", "monitor_01", "display_01"]),
    # Glass & Transparency
    ("glass", ["glass_01_a", "glass_transparent_01", "glass_01", "glass", "m_glass"]),
    ("window", ["glass_01_a", "glass_01", "window_01"]),
    ("water", ["water_01", "waterfall_01", "fx_water_01"]),
    ("ice", ["ice_01", "crystal_01", "glass_01"]),
    # Debris & Trash
    ("trash", ["trash_01", "junk_01"]),
    ("junk_large", ["junk_large_01", "junk_01"]),
    ("junk", ["junk_01"]),
    # FX & Beams
    ("laser_grid", ["laser_grid_01", "laser_01"]),
    ("laser", ["laser_01", "fx_laser_01"]),
    ("fx_leaf", ["fx_leaves_01", "fx_leaves_02", "fx_leaves_03"]),
    ("fx_leaves", ["fx_leaves_01", "fx_leaves_02", "fx_leaves_03"]),
    ("fx_lightray", ["fx_lightray_01", "fx_lightray_02"]),
    ("fx_fish", ["fx_fish_pixel_01"]),
    ("fx_gradient", ["fx_gradient_01"]),
    ("fx_sunbeam", ["fx_sunbeam_01", "fx_lightray_01"]),
    ("fx_sparkle", ["fx_sparkle_01"]),
    ("fx_streak", ["fx_streaks_01"]),
    ("fx_", ["fx_01", "fx_particles_01"]),
    # Modular Walls & Triplanar
    ("sm_bld_block_", ["wall_01_triplanar", "wall_01_alt_01_triplanar", "wall_01_a", "building_01"]),
    ("triplanar", ["wall_01_triplanar", "wall_01_a", "ground_01"]),
    ("parallax", ["parallax_full_01", "parallax_01", "parallax"]),
    # Sky & Environment
    ("skybox", ["skybox_01", "skybox_02", "sky_01"]),
    ("skydome", ["skydome_01", "sky_01", "simplesky"]),
]

# Precompiled Regexes for High-Performance Parsing
RE_FBX_MAT1 = re.compile(b"([a-zA-Z0-9_-]+)\x00\x01Material")
RE_FBX_MAT2 = re.compile(b"Material::([a-zA-Z0-9_-]+)")
RE_FBX_MAT3 = re.compile(b"Material[\x00-\x10]+([a-zA-Z0-9_ -]{2,40})[\x00-\x10]+(?:FbxSurfaceLambert|FbxSurfacePhong|Material)")
RE_FBX_MAT4 = re.compile(b"([a-zA-Z0-9_ -]{2,40})\x00\x01(?:FbxSurfaceLambert|FbxSurfacePhong)")
RE_FBX_MAT5 = re.compile(b"Material\x00+([a-zA-Z0-9_-]+)")
RE_FBX_LINES = re.compile(r"fbx/(?:importer|allow_geometry_helper_nodes|embedded_image_handling|naming_version)=.*\n?")
RE_EXT_PATH = re.compile(r'path="([^"]+)"')
RE_EXT_UID = re.compile(r'uid="([^"]+)"')
RE_SRC_FILE = re.compile(r'source_file="([^"]+)"')
RE_NUM_EXT = re.compile(r"(\d+)")
RE_RES_TEX = re.compile(r'path="res://([^"]+\.(?:png|psd|tga|jpg|jpeg|webp))"', re.IGNORECASE)
RE_CLEAN_PREFIX = re.compile(r"^(?:mat_|m_|shd_|lambert_|standardsurface_|pasted__|roboguy_)+", re.IGNORECASE)
RE_CLEAN_SUFFIX = re.compile(r"(?:_sg\d+|sg\d+|\.mat|\.tres)+$", re.IGNORECASE)


# ==============================================================================
# Helper: Atomic File Transformer
# ==============================================================================
def transform_file(file_path: str, transform_fn: Callable[[str], Optional[str]]) -> bool:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
        res = transform_fn(raw)
        if res is not None and res != raw:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(res)
            return True
    except Exception:
        pass
    return False


def get_pack_root(file_path: str) -> str:
    parts = file_path.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        low = p.lower()
        if low in ("synty", "assets") and i + 1 < len(parts):
            if parts[i + 1].lower() == "synty" and i + 2 < len(parts):
                return "/".join(parts[:i + 3])
            return "/".join(parts[:i + 2])
        if low.startswith("polygon") or low.startswith("simple") or low.startswith("sidekick"):
            return "/".join(parts[:i + 1])
    return os.path.dirname(os.path.dirname(file_path))


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
# 2. Universal Texture Classifier & Dynamic Missing Resource Resolver
# ==============================================================================
def save_image_safe(img, target_path: str) -> None:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if not HAS_PIL or img is None:
        try:
            with open(target_path, "wb") as fh:
                fh.write(PNG_1X1_BYTES)
        except Exception:
            pass
        return

    ext = os.path.splitext(target_path)[1].lower()
    fmt = "PNG" if ext == ".png" else ("TGA" if ext == ".tga" else "JPEG")
    try:
        img.save(target_path, format=fmt)
    except Exception:
        pass


def sanitize_and_resolve_textures(project_root: str, categorized_files: Dict[str, List[str]]) -> Tuple[int, int, int]:
    fixed_formats = aliases_created = normalized_imports = 0
    ext_to_fmt = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}

    # Group color atlases by pack root
    pack_atlases: Dict[str, str] = {}
    all_atlases: List[str] = []

    for p in categorized_files.get("images", []):
        low = os.path.basename(p).lower()
        pack_dir = get_pack_root(p)
        is_atlas = any(k in low for k in ["colormap", "texture_01", "base_color", "diffuse", "palette", "atlas", "main"]) and "dst" not in low
        if is_atlas:
            pack_atlases.setdefault(pack_dir, p)
            all_atlases.append(p)

    global_default_atlas = next(iter(pack_atlases.values()), all_atlases[0] if all_atlases else None)

    # 1. Format check & repair (misnamed files)
    def check_image(p: str) -> int:
        if not HAS_PIL:
            return 0
        ext = os.path.splitext(p)[1].lower()
        if ext in ext_to_fmt or ext == ".tga":
            try:
                with open(p, "rb") as fh:
                    hdr = fh.read(16)
                mismatch = (ext in ext_to_fmt and not hdr.startswith(ext_to_fmt[ext])) or \
                           (ext == ".tga" and (hdr.startswith(b"\x89PNG") or hdr.startswith(b"\xff\xd8")))
                if mismatch:
                    with Image.open(p) as img:
                        save_image_safe(img, p)
                        return 1
            except Exception:
                pass
        return 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fixed_formats = sum(ex.map(check_image, categorized_files.get("images", [])))

    # 2. Dynamic Missing Texture Discovery across all .tres, .mat.tres, .tscn
    referenced_textures: Set[str] = set()

    def scan_tex_refs(p: str) -> None:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            for rel in RE_RES_TEX.findall(txt):
                referenced_textures.add(rel)
        except Exception:
            pass

    scan_targets = categorized_files.get("materials", []) + categorized_files.get("tscn", [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(scan_tex_refs, scan_targets))

    # Also add common Synty legacy workstation paths
    legacy_stubs = [
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
    for ls in legacy_stubs:
        referenced_textures.add(ls)

    # Dynamic Provisioning of Missing Textures
    for rel in referenced_textures:
        full_target = os.path.join(project_root, rel)
        if not os.path.exists(full_target):
            pack_dir = get_pack_root(full_target)
            src_atlas = pack_atlases.get(pack_dir) or global_default_atlas
            try:
                if HAS_PIL and src_atlas and os.path.exists(src_atlas):
                    with Image.open(src_atlas) as img:
                        save_image_safe(img, full_target)
                else:
                    save_image_safe(None, full_target)
                aliases_created += 1
            except Exception:
                pass

    # 3. Texture .import normalizer (sRGB / normal map compression)
    def check_texture_import(p: str) -> int:
        if "normal" in p.lower():
            return 0
        def _modify(txt: str) -> str:
            txt = txt.replace("valid=false\n", "").replace("valid=false", "")
            txt = re.sub(r"compress/normal_map=\d+", "compress/normal_map=0", txt)
            txt = re.sub(r"roughness/mode=\d+", "roughness/mode=0", txt)
            return txt
        return 1 if transform_file(p, _modify) else 0

    tex_imports = [p for p in categorized_files.get("imports", []) if any(p.endswith(ext + ".import") for ext in [".png", ".tga", ".jpg", ".webp"])]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        normalized_imports = sum(ex.map(check_texture_import, tex_imports))

    # 4. Triplanar material normalizer (enables world-triplanar projection)
    def check_triplanar_material(p: str) -> int:
        if "triplanar" not in p.lower():
            return 0
        def _modify(txt: str) -> str:
            txt = txt.replace("uv1_triplanar = false", "uv1_triplanar = true")
            txt = txt.replace("uv1_world_triplanar = false", "uv1_world_triplanar = true")
            txt = txt.replace("uv1_scale = Vector3(1, 1, 1)", "uv1_scale = Vector3(0.5, 0.5, 0.5)")
            return txt
        return 1 if transform_file(p, _modify) else 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(check_triplanar_material, categorized_files.get("materials", [])))

    return fixed_formats, aliases_created, normalized_imports


# ==============================================================================
# 3. Dynamic Material Generator (For packs lacking Godot .mat.tres)
# ==============================================================================
def ensure_pack_materials(project_root: str, categorized_files: Dict[str, List[str]]) -> int:
    generated_count = 0
    # Map packs to their images and materials
    pack_images: Dict[str, List[str]] = {}
    pack_mats: Dict[str, List[str]] = {}

    for img in categorized_files.get("images", []):
        pack_images.setdefault(get_pack_root(img), []).append(img)

    for mat in categorized_files.get("materials", []):
        pack_mats.setdefault(get_pack_root(mat), []).append(mat)

    for pack_dir, images in pack_images.items():
        existing_mats = pack_mats.get(pack_dir, [])
        if len(existing_mats) == 0 and len(images) > 0:
            mat_folder = os.path.join(pack_dir, "Materials")
            os.makedirs(mat_folder, exist_ok=True)
            for img_path in images:
                stem = os.path.splitext(os.path.basename(img_path))[0]
                mat_file = os.path.join(mat_folder, f"{stem}.mat.tres")
                if not os.path.exists(mat_file):
                    rel_tex = "res://" + os.path.relpath(img_path, project_root).replace("\\", "/")
                    is_triplanar = "triplanar" in stem.lower() or "wall" in stem.lower()
                    is_emissive = "emissive" in stem.lower() or "glow" in stem.lower()
                    is_glass = "glass" in stem.lower() or "transparent" in stem.lower()

                    mat_content = f"""[gd_resource type="StandardMaterial3D" load_steps=2 format=3]

[ext_resource type="Texture2D" path="{rel_tex}" id="1_tex"]

[resource]
resource_name = "{stem}"
cull_mode = 0
roughness = 0.85
albedo_texture = ExtResource("1_tex")
"""
                    if is_triplanar:
                        mat_content += "uv1_triplanar = true\nuv1_world_triplanar = true\nuv1_scale = Vector3(0.5, 0.5, 0.5)\n"
                    if is_emissive:
                        mat_content += "emission_enabled = true\nemission = Color(1, 1, 1, 1)\nemission_energy_multiplier = 2.0\nemission_texture = ExtResource(\"1_tex\")\n"
                    if is_glass:
                        mat_content += "transparency = 1\nalbedo_color = Color(1, 1, 1, 0.5)\n"

                    try:
                        with open(mat_file, "w", encoding="utf-8") as fh:
                            fh.write(mat_content)
                        categorized_files["materials"].append(mat_file)
                        generated_count += 1
                    except Exception:
                        pass

    return generated_count


# ==============================================================================
# 4. Universal FBX Binary Material Slot Parser & 4-Tier Matcher
# ==============================================================================
def extract_fbx_material_slots(fbx_path: str) -> Set[str]:
    slots = set()
    try:
        with open(fbx_path, "rb") as fh:
            data = fh.read(1024 * 1024)
        for rx in (RE_FBX_MAT1, RE_FBX_MAT2, RE_FBX_MAT5):
            for m in rx.findall(data):
                slots.add(m.decode("ascii", errors="ignore"))
        for rx in (RE_FBX_MAT3, RE_FBX_MAT4):
            for m in rx.findall(data):
                slots.add(m.decode("ascii", errors="ignore").strip())
    except Exception:
        pass

    slots.update(FALLBACK_SLOTS)
    return {s for s in slots if len(s) >= 2 and not s.startswith(" ")}


def resolve_slot_material(slot: str, fbx_name: str, mats: Dict[str, str], default_atlas: str) -> str:
    s_low = slot.lower()
    f_low = fbx_name.lower().replace(".fbx", "")

    # Tier 1: Exact Match
    if s_low in mats:
        return mats[s_low]

    # Tier 2: Normalized Name Match (strip Maya/Max prefixes and suffixes)
    norm_slot = RE_CLEAN_PREFIX.sub("", s_low)
    norm_slot = RE_CLEAN_SUFFIX.sub("", norm_slot)
    if norm_slot in mats:
        return mats[norm_slot]
    for k, v in mats.items():
        if norm_slot in k or k in norm_slot:
            return v

    # Tier 3: Universal Semantic Category Matching
    for tag, candidates in SEMANTIC_RULES:
        if tag in s_low or tag in f_low:
            for cand in candidates:
                if cand in mats:
                    return mats[cand]
                for k, v in mats.items():
                    if cand in k:
                        return v

    if any(k in s_low for k in ["wall", "a_wall", "brick", "stucco", "floor", "building"]):
        num_m = RE_NUM_EXT.search(slot)
        if num_m:
            target_key = f"wall_{num_m.group(1).zfill(2)}"
            for k in [f"{target_key}_a", f"{target_key}_b", target_key]:
                if k in mats:
                    return mats[k]
        for k, v in mats.items():
            if any(term in k for term in ["wall_01_a", "wall", "brick", "floor", "building"]):
                return v

    if any(k in s_low for k in ["tree", "rock", "mountain", "water", "wood", "nature"]):
        for k, v in mats.items():
            if any(term in k for term in ["tree", "rock", "mountain", "water", "nature", "wood"]):
                return v

    for sfx in ["_01_b", "_01_c", "_02_a", "_02_b", "_02_c", "_03_a", "_03_b", "_03_c", "_04_a", "_04_b", "_04_c"]:
        if f_low.endswith(sfx):
            for k, v in mats.items():
                if sfx[1:] in k:
                    return v

    # Tier 4: Pack Default Color Atlas
    return default_atlas


def clean_import_file(content: str) -> str:
    content = content.replace("valid=false\n", "").replace("valid=false", "")
    content = RE_FBX_LINES.sub("", content)

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
        content = content[:params_idx + 8] + "\nfbx/importer=0\nfbx/embedded_image_handling=0\nmaterials/extract=0" + content[params_idx + 8:]

    return content.strip()


def map_all_fbx_materials(project_root: str, categorized_files: Dict[str, List[str]]) -> Tuple[int, int]:
    pack_materials: Dict[str, Dict[str, str]] = {}
    all_materials: Dict[str, str] = {}

    for p in categorized_files.get("materials", []):
        stem = os.path.basename(p).replace(".mat.tres", "").replace(".tres", "").lower()
        rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
        pack_dir = get_pack_root(p)
        pack_materials.setdefault(pack_dir, {})[stem] = rel
        all_materials[stem] = rel

    global_default = next((v for k, v in all_materials.items() if "01_a" in k or "colormap" in k), next(iter(all_materials.values()), ""))

    def process_fbx(p: str) -> Tuple[int, int]:
        imp_path = p + ".import"
        if not os.path.exists(imp_path):
            return 0, 0

        pack_dir = get_pack_root(p)
        mats = pack_materials.get(pack_dir, all_materials)
        default_atlas = next((v for k, v in mats.items() if "01_a" in k or "colormap" in k), global_default)

        slots = extract_fbx_material_slots(p)
        fbx_name = os.path.basename(p)
        mat_lines = [
            f'"{s}": {{\n"use_external/enabled": true,\n"use_external/path": "{resolve_slot_material(s, fbx_name, mats, default_atlas)}"\n}}'
            for s in sorted(slots)
        ]

        sub_body = ",\n".join(mat_lines)
        def _modify(raw: str) -> str:
            return clean_import_file(raw) + f'\n\n_subresources={{\n"materials": {{\n{sub_body}\n}}\n}}\n'

        transform_file(imp_path, _modify)
        return 1, len(mat_lines)

    fbx_list = categorized_files.get("fbx", [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(process_fbx, fbx_list))

    total_models = sum(r[0] for r in results)
    total_slots = sum(r[1] for r in results)
    return total_models, total_slots


# ==============================================================================
# 5. Universal Character Rig & Mesh Visibility Resolver
# ==============================================================================
def fix_character_rigs_and_visibility(categorized_files: Dict[str, List[str]]) -> Tuple[int, int]:
    # 1. Universal Skeleton Renaming (GeneralSkeleton -> Skeleton3D)
    def process_tscn(p: str) -> int:
        def _modify(txt: str) -> Optional[str]:
            if "GeneralSkeleton" not in txt:
                return None
            txt = txt.replace('parent="GeneralSkeleton"', 'parent="Skeleton3D"')
            txt = txt.replace('"GeneralSkeleton"', '"Skeleton3D"')
            txt = txt.replace("GeneralSkeleton/", "Skeleton3D/")
            return txt
        return 1 if transform_file(p, _modify) else 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fixed_skels = sum(ex.map(process_tscn, categorized_files.get("tscn", [])))

    # 2. Universal Character Mesh Visibility Selection in Prefabs
    def process_prefab(p: str) -> int:
        stem = os.path.basename(p).split(".")[0]
        def _modify_prefab(txt: str) -> Optional[str]:
            # Check for multi-character meshes under Skeleton3D
            char_nodes = re.findall(r'\[node name="([^"]+)"[^\]]*parent="Skeleton3D"[^\]]*\]', txt)
            if len(char_nodes) <= 1:
                return None
            modified = False
            for cname in char_nodes:
                is_target = (cname.lower() == stem.lower() or cname.lower() in stem.lower())
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
            return txt if modified else None
        return 1 if transform_file(p, _modify_prefab) else 0

    prefabs = [p for p in categorized_files.get("tscn", []) if p.endswith(".prefab.tscn")]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fixed_prefabs = sum(ex.map(process_prefab, prefabs))

    return fixed_skels, fixed_prefabs


# ==============================================================================
# 6. Scene & Resource UID Synchronizer
# ==============================================================================
def synchronize_uids(project_root: str, categorized_files: Dict[str, List[str]]) -> Tuple[int, int]:
    path_to_uid: Dict[str, str] = {}

    def extract_uid(p: str) -> Optional[Tuple[str, str]]:
        try:
            if p.endswith(".import"):
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
                uid_m = RE_EXT_UID.search(txt)
                src_m = RE_SRC_FILE.search(txt)
                if uid_m and src_m:
                    return src_m.group(1), uid_m.group(1)
            elif p.endswith(".tscn"):
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    fline = fh.readline()
                uid_m = RE_EXT_UID.search(fline)
                if uid_m:
                    rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
                    return rel, uid_m.group(1)
        except Exception:
            pass
        return None

    scan_targets = categorized_files.get("imports", []) + categorized_files.get("tscn", [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(extract_uid, scan_targets):
            if res:
                path_to_uid[res[0]] = res[1]

    def sync_scene(p: str) -> Tuple[int, int]:
        u_fixed = [0]
        def _modify(txt: str) -> Optional[str]:
            if "[ext_resource" not in txt:
                return None
            lines = txt.splitlines()
            modified = False
            new_lines = []
            for line in lines:
                if line.startswith("[ext_resource"):
                    p_m = RE_EXT_PATH.search(line)
                    u_m = RE_EXT_UID.search(line)
                    if p_m and p_m.group(1) in path_to_uid:
                        target_uid = path_to_uid[p_m.group(1)]
                        if u_m:
                            if u_m.group(1) != target_uid:
                                line = line.replace(u_m.group(1), target_uid)
                                modified = True
                                u_fixed[0] += 1
                        else:
                            line = line.replace("[ext_resource ", f'[ext_resource uid="{target_uid}" ')
                            modified = True
                            u_fixed[0] += 1
                new_lines.append(line)
            return "\n".join(new_lines) + "\n" if modified else None

        modified = transform_file(p, _modify)
        return (1 if modified else 0), u_fixed[0]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(sync_scene, categorized_files.get("tscn", [])))

    updated_scenes = sum(r[0] for r in results)
    uids_fixed = sum(r[1] for r in results)
    return updated_scenes, uids_fixed


# ==============================================================================
# Pipeline Coordinator
# ==============================================================================
def collect_project_files(project_root: str) -> Dict[str, List[str]]:
    categorized = {
        "fbx": [],
        "tscn": [],
        "imports": [],
        "images": [],
        "materials": [],
    }
    image_exts = {".png", ".tga", ".jpg", ".jpeg", ".webp"}

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            full_path = os.path.join(root, f)
            if f.endswith(".fbx"):
                categorized["fbx"].append(full_path)
            elif f.endswith(".tscn"):
                categorized["tscn"].append(full_path)
            elif f.endswith(".import"):
                categorized["imports"].append(full_path)
            elif f.endswith((".mat.tres", ".tres")) and not f.endswith(".mesh"):
                categorized["materials"].append(full_path)
            elif os.path.splitext(f)[1].lower() in image_exts:
                categorized["images"].append(full_path)

    return categorized


def run_pipeline(project_root: str, package_path: Optional[str] = None, purge_cache: bool = False) -> None:
    project_root = os.path.abspath(project_root)
    print("==================================================", flush=True)
    print("       Godot 4 Universal Synty Automator          ", flush=True)
    print("==================================================", flush=True)
    print(f"Target Project: {project_root}", flush=True)

    if not os.path.exists(os.path.join(project_root, "project.godot")):
        print(f"ERROR: No project.godot found at '{project_root}'!", flush=True)
        sys.exit(1)

    if package_path:
        print(f"\n[0/5] Extracting UnityPackage: {os.path.basename(package_path)}...", flush=True)
        extracted = extract_unitypackage(package_path, project_root)
        print(f"      - Extracted {extracted} files into project.", flush=True)

    # Fast single-pass filesystem scan
    categorized_files = collect_project_files(project_root)

    print("\n[1/5] Sanitizing Textures & Resolving Missing Resources...", flush=True)
    if not HAS_PIL:
        print("      (Note: Pillow library not detected. Install with 'pip install Pillow' to enable image re-encoding.)", flush=True)
    fixed_tex, aliases, norm_tex = sanitize_and_resolve_textures(project_root, categorized_files)
    print(f"      - Fixed misnamed image formats: {fixed_tex}", flush=True)
    print(f"      - Generated missing texture & PSD alias stubs: {aliases}", flush=True)
    print(f"      - Normalized sRGB & normal map import settings: {norm_tex}", flush=True)

    print("\n[2/5] Checking Pack Materials & Generating Missing StandardMaterial3D...", flush=True)
    gen_mats = ensure_pack_materials(project_root, categorized_files)
    print(f"      - Auto-generated {gen_mats} StandardMaterial3D resources for unconfigured packs.", flush=True)

    print("\n[3/5] Deep Scanning & Mapping FBX Material Slots...", flush=True)
    models, slots = map_all_fbx_materials(project_root, categorized_files)
    print(f"      - Mapped {slots} material slots across {models} FBX models.", flush=True)

    print("\n[4/5] Rectifying Character Rigs & Multi-Mesh Visibility...", flush=True)
    fixed_skels, fixed_chars = fix_character_rigs_and_visibility(categorized_files)
    print(f"      - Updated GeneralSkeleton -> Skeleton3D in {fixed_skels} scenes/prefabs.", flush=True)
    print(f"      - Applied selective mesh visibility to {fixed_chars} character prefabs.", flush=True)

    print("\n[5/5] Synchronizing Scene & Resource UIDs...", flush=True)
    synced_scenes, fixed_uids = synchronize_uids(project_root, categorized_files)
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
    print("Universal Synty automation completed successfully!", flush=True)
    print("Reload the project in Godot or restart the editor.", flush=True)
    print("==================================================", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Universal Synty asset importer and configuration engine for Godot 4.")
    parser.add_argument("--path", type=str, default=os.getcwd(), help="Path to Godot project root.")
    parser.add_argument("--package", "-pkg", type=str, default=None, help="Path to .unitypackage file to extract and import.")
    parser.add_argument("--purge-cache", action="store_true", help="Delete cached .scn files in .godot/imported.")
    args = parser.parse_args()
    run_pipeline(args.path, package_path=args.package, purge_cache=args.purge_cache)


if __name__ == "__main__":
    main()
