#!/usr/bin/env python3
"""
Godot Synty Importer & Automator (Universal Deterministic Engine)
================================================================
Autonomous, universal importer and configuration engine for all Synty Studios
3D asset packs in Godot 4.

Handles:
  - Direct .unitypackage extraction & 100% deterministic YAML .mat / .prefab parsing
  - Hierarchical pack-scoped material & prefab dependency isolation
  - Automated StandardMaterial3D generation & corrupt placeholder material repair
  - Deep FBX binary connection graph parsing & deterministic material slot mapping
  - Universal multi-character rig hierarchy & selective mesh visibility
  - Project-wide scene & resource UID synchronization
  - World-triplanar UV & normal map import normalization
  - Direct Unity Scene (.unity) to Godot Scene (.tscn) compilation (Overview, Demo_City, etc.)
  - Headless cache purging for seamless Godot editor reloads

Usage:
    python3 addons/synty_importer/synty_automator.py [--path /path/to/godot_project] [--extract-all] [--package /path/to/pack.unitypackage] [--purge-cache]
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tarfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IGNORED_DIRS = {".godot", ".git", "node_modules", ".import"}
MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)

PNG_1X1_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc````"
    b"\x00\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
)

TGA_1X1_BYTES = (
    b"\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x01\x00\x20\x08"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00TRUEVISION-XFILE.\x00"
)

TEX_STRIP_WORDS = ("texture", "colormap", "basecolor", "diffuse", "palette", "atlas", "main")
RE_FBX_LINES = re.compile(r"fbx/(?:importer|allow_geometry_helper_nodes|embedded_image_handling|naming_version)=.*\n?")
RE_EXT_PATH = re.compile(r'path="([^"]+)"')
RE_EXT_UID = re.compile(r'uid="([^"]+)"')
RE_SRC_FILE = re.compile(r'source_file="([^"]+)"')
RE_NUM_EXT = re.compile(r"(\d+)")
RE_RES_TEX = re.compile(r'path="res://([^"]+\.(?:png|tga|jpg|jpeg|webp))"', re.IGNORECASE)
RE_CLEAN_PREFIX = re.compile(r"^(?:mat_|m_|shd_|lambert_|standardsurface_|pasted__)+", re.IGNORECASE)
RE_CLEAN_SUFFIX = re.compile(r"(?:_sg\d+|sg\d+|\.mat|\.tres)+$", re.IGNORECASE)

_ERRORS = 0

def fail_warn(message: str) -> None:
    global _ERRORS
    _ERRORS += 1
    print(f"      [WARN] {message}", file=sys.stderr, flush=True)


def transform_file(file_path: str, transform_fn: Callable[[str], Optional[str]]) -> bool:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
        res = transform_fn(raw)
        if res is not None and res != raw:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(res)
            return True
    except Exception as exc:
        fail_warn(f"Failed to transform {file_path}: {exc}")
    return False


def get_pack_root(file_path: str) -> str:
    dir_path = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
    parts = dir_path.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        low = p.lower()
        if low in ("synty", "assets") and i + 1 < len(parts):
            if parts[i + 1].lower() == "synty" and i + 2 < len(parts):
                return "/".join(parts[:i + 3])
            return "/".join(parts[:i + 2])
        if low.startswith("polygon") or low.startswith("simple") or low.startswith("sidekick"):
            return "/".join(parts[:i + 1])
    return dir_path


def normalize_tex_stem(name: str) -> str:
    low = name.lower()
    for word in TEX_STRIP_WORDS:
        low = low.replace(word, "")
    digits = re.findall(r"\d+", low)
    if len(digits) == 1 and len(digits[0]) == 1:
        low = low.replace(digits[0], f"0{digits[0]}")
    return re.sub(r"[^a-z0-9]", "", low)


ATLAS_INCLUDE_KEYWORDS = ("colormap", "texture_01", "base_color", "diffuse", "palette", "atlas", "main", "01_a")
ATLAS_EXCLUDE_TOKENS = ("dst", "hack", "branch", "glass", "damage", "alt", "skin", "poster", "wall", "floor", "brick", "junk", "trash", "gradient", "holo")


def looks_like_atlas(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in ATLAS_INCLUDE_KEYWORDS) and not any(t in low for t in ATLAS_EXCLUDE_TOKENS)


def quat_to_basis(x: float, y: float, z: float, w: float, sx: float = 1.0, sy: float = 1.0, sz: float = 1.0) -> Tuple[float, ...]:
    length = (x * x + y * y + z * z + w * w) ** 0.5
    if length > 0.00001:
        x, y, z, w = x / length, y / length, z / length, w / length
    else:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0

    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z

    r00 = (1.0 - 2.0 * (yy + zz)) * sx
    r01 = (2.0 * (xy - wz)) * sy
    r02 = (2.0 * (xz + wy)) * sz

    r10 = (2.0 * (xy + wz)) * sx
    r11 = (1.0 - 2.0 * (xx + zz)) * sy
    r12 = (2.0 * (yz - wx)) * sz

    r20 = (2.0 * (xz - wy)) * sx
    r21 = (2.0 * (yz + wx)) * sy
    r22 = (1.0 - 2.0 * (xx + yy)) * sz

    return (r00, r01, r02, r10, r11, r12, r20, r21, r22)


def unity_to_godot_transform(
    position: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
    """Convert a Unity world transform to Godot's imported FBX convention.

    Godot's FBX importer bakes Synty meshes in the -X convention. Unity
    positions therefore negate X, while Unity quaternions negate Y and Z;
    W is preserved. Keeping this conversion centralized prevents scene nodes,
    lights, and cameras from using different mirror axes.
    """
    px, py, pz = position
    qx, qy, qz, qw = rotation
    return (-px, py, pz), (qx, -qy, -qz, qw)


# ==============================================================================
# 1. UnityPackage Extraction & Deterministic YAML Parser
# ==============================================================================
def read_unitypackage_data(package_path: str) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    guid_to_path: Dict[str, str] = {}
    guid_to_asset: Dict[str, bytes] = {}
    try:
        with tarfile.open(package_path, "r|*") as tar:
            for member in tar:
                if member.isfile():
                    parts = member.name.replace("\\", "/").split("/")
                    if len(parts) >= 2:
                        guid, kind = parts[0], parts[1]
                        if kind in ("pathname", "asset"):
                            f = tar.extractfile(member)
                            if f:
                                content = f.read()
                                if kind == "pathname":
                                    guid_to_path[guid] = content.decode("utf-8", errors="ignore").splitlines()[0].strip()
                                elif kind == "asset":
                                    guid_to_asset[guid] = content
    except Exception as exc:
        fail_warn(f"Failed to read package {package_path}: {exc}")
    return guid_to_path, guid_to_asset


def extract_unitypackage(package_path: str, destination_root: str) -> int:
    if not os.path.exists(package_path):
        raise FileNotFoundError(f"Package not found: {package_path}")

    guid_to_path, guid_to_asset = read_unitypackage_data(package_path)

    def write_asset(item: Tuple[str, str]) -> int:
        guid, rel = item
        if not rel or guid not in guid_to_asset:
            return 0
        target = os.path.join(destination_root, rel)
        if not os.path.realpath(target).startswith(os.path.realpath(destination_root) + os.sep):
            return 0
        if os.path.exists(target):
            return 0
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            with open(target, "wb") as out:
                out.write(guid_to_asset[guid])
            return 1
        except Exception as exc:
            fail_warn(f"Failed to extract {rel}: {exc}")
            return 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        extracted = sum(ex.map(write_asset, guid_to_path.items()))
    return extracted


def parse_unity_mat_yaml(raw_yaml: str, guid_to_path: Dict[str, str]) -> Dict[str, Any]:
    texs: Dict[str, str] = {}
    for block in re.finditer(r"- (_[A-Za-z0-9_]+):\s*\n\s*m_Texture:\s*\{fileID:\s*2800000,\s*guid:\s*([a-f0-9]{32})", raw_yaml):
        prop_name, tguid = block.group(1), block.group(2)
        if tguid in guid_to_path:
            texs[prop_name] = guid_to_path[tguid]

    colors: Dict[str, Tuple[float, float, float, float]] = {}
    for block in re.finditer(r"(_[A-Za-z0-9_]+):\s*\{r:\s*([\d.-]+),\s*g:\s*([\d.-]+),\s*b:\s*([\d.-]+),\s*a:\s*([\d.-]+)\}", raw_yaml):
        pname = block.group(1)
        colors[pname] = (float(block.group(2)), float(block.group(3)), float(block.group(4)), float(block.group(5)))

    floats: Dict[str, float] = {}
    for block in re.finditer(r"- (_[A-Za-z0-9_]+):\s*([\d.-]+)", raw_yaml):
        pname, val = block.group(1), float(block.group(2))
        floats[pname] = val

    is_transparent = "_SURFACE_TYPE_TRANSPARENT" in raw_yaml or "_BLENDMODE_ALPHA" in raw_yaml or "_ALPHAPREMULTIPLY_ON" in raw_yaml
    is_cutout = "_ALPHATEST_ON" in raw_yaml
    is_holo = "Hologram" in raw_yaml or "_Neon_Color" in colors or "_Holo_Lines" in texs

    return {
        "texs": texs,
        "colors": colors,
        "floats": floats,
        "is_transparent": is_transparent,
        "is_cutout": is_cutout,
        "is_holo": is_holo,
    }


def resolve_texture_res_path(
    tex_rel_path: Optional[str],
    project_root: str,
    pack_dir: str,
    image_lookup: Optional[Dict[Tuple[str, str], str]] = None
) -> Optional[str]:
    if not tex_rel_path or tex_rel_path.lower().endswith(".psd"):
        return None
    clean_rel = tex_rel_path.replace("\\", "/").lstrip("/")
    full_path = os.path.join(project_root, clean_rel)
    if os.path.exists(full_path):
        return "res://" + clean_rel
    fname = os.path.basename(clean_rel).lower()
    norm_pdir = os.path.normpath(pack_dir)
    if image_lookup and (norm_pdir, fname) in image_lookup:
        found_p = image_lookup[(norm_pdir, fname)]
        return "res://" + os.path.relpath(found_p, project_root).replace("\\", "/")
    return "res://" + clean_rel if os.path.exists(full_path) else None


def generate_godot_material_from_unity(
    mat_stem: str,
    parsed: Dict[str, Any],
    project_root: str,
    pack_dir: str,
    default_atlas: Optional[str] = None,
    image_lookup: Optional[Dict[Tuple[str, str], str]] = None
) -> str:
    texs = parsed["texs"]
    colors = parsed["colors"]
    floats = parsed["floats"]
    is_holo = parsed["is_holo"]
    is_transparent = parsed["is_transparent"]
    is_cutout = parsed["is_cutout"]

    ext_resources = []
    properties = []
    res_idx = 1
    norm_pdir = os.path.normpath(pack_dir)

    # 1. Albedo Texture & Color
    albedo_res_id = None
    albedo_tex_path = (
        texs.get("_Albedo_Map") or texs.get("_MainTex") or texs.get("_BaseMap") or
        texs.get("_ColorMap") or texs.get("_BaseColorMap") or texs.get("_Main_Tex") or
        texs.get("_Wall_Texture")
    )
    if not albedo_tex_path and is_holo:
        albedo_tex_path = texs.get("_Holo_Lines")

    if not albedo_tex_path and image_lookup:
        mat_clean = normalize_tex_stem(mat_stem.lower())
        matched_img = image_lookup.get((norm_pdir, mat_clean)) or image_lookup.get((norm_pdir, mat_stem.lower()))
        if matched_img:
            albedo_tex_path = os.path.relpath(matched_img, project_root).replace("\\", "/")
        elif default_atlas and not any(k in mat_stem.lower() for k in ["glass", "transparent", "blank", "skybox", "laser"]):
            albedo_tex_path = os.path.relpath(default_atlas, project_root).replace("\\", "/")

    if albedo_tex_path:
        rel_albedo = resolve_texture_res_path(albedo_tex_path, project_root, pack_dir, image_lookup)
        if rel_albedo:
            albedo_res_id = f"{res_idx}_tex"
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_albedo}" id="{albedo_res_id}"]')
            properties.append(f'albedo_texture = ExtResource("{albedo_res_id}")')
            res_idx += 1

    base_col = colors.get("_Base_Color") or colors.get("_BaseColor") or colors.get("_Color")
    if base_col and not is_holo:
        properties.append(f'albedo_color = Color({base_col[0]:.6g}, {base_col[1]:.6g}, {base_col[2]:.6g}, {base_col[3]:.6g})')

    # 2. Transparency, Cull, and Shading
    mat_low = mat_stem.lower()
    is_glass = "glass" in mat_low or "transparent" in mat_low or "water" in mat_low
    is_cutout_name = any(k in mat_low for k in ["junk", "trash", "fence", "chain", "leaves", "foliage", "plant", "decal", "graffiti", "wire", "grid", "cutout"])

    if is_glass or is_holo or is_transparent:
        properties.append("transparency = 1")
    elif is_cutout_name:
        properties.append("transparency = 2")
        properties.append(f'alpha_scissor_threshold = {floats.get("_Cutoff", 0.5):.6g}')

    properties.append("cull_mode = 2")

    if is_holo:
        properties.append("shading_mode = 0")

    # 3. Normal Map
    norm_path = texs.get("_Normal_Map") or texs.get("_BumpMap") or texs.get("_Normals_Map")
    if norm_path and not any(k in mat_low for k in ["hologram", "glass", "fx_"]):
        rel_norm = resolve_texture_res_path(norm_path, project_root, pack_dir, image_lookup)
        if rel_norm:
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_norm}" id="{res_idx}_normal"]')
            properties.append("normal_enabled = true")
            properties.append(f'normal_texture = ExtResource("{res_idx}_normal")')
            res_idx += 1

    # 4. Occlusion (AO)
    ao_path = texs.get("_OcclusionMap") or texs.get("_AO_Map") or texs.get("_AOMap")
    if ao_path:
        rel_ao = resolve_texture_res_path(ao_path, project_root, pack_dir, image_lookup)
        if rel_ao:
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_ao}" id="{res_idx}_ao"]')
            properties.append("ao_enabled = true")
            properties.append(f'ao_texture = ExtResource("{res_idx}_ao")')
            res_idx += 1

    # 5. Emission
    is_neon_mat = any(k in mat_low for k in ["hologram", "holo", "neon", "laser", "glow", "light", "emissive", "screen", "monitor", "billboard", "sign", "lamp", "vending"])
    emissive_tex_path = (
        texs.get("_Emission_Mask") or texs.get("_Emission_Map") or
        texs.get("_EmissionMap")
    )
    neon_col = colors.get("_Neon_Color") or colors.get("_Neon_Colour_01")

    if emissive_tex_path and is_neon_mat:
        rel_em = resolve_texture_res_path(emissive_tex_path, project_root, pack_dir, image_lookup)
        if rel_em:
            properties.append("emission_enabled = true")
            em_col = neon_col or colors.get("_EmissionColor") or colors.get("_Emission_Color") or (1.0, 1.0, 1.0, 1.0)
            properties.append(f'emission = Color({em_col[0]:.6g}, {em_col[1]:.6g}, {em_col[2]:.6g}, 1)')
            em_power = floats.get("_Emission_Power", floats.get("_EmissionStrength", 1.0))
            properties.append(f'emission_energy_multiplier = {em_power:.6g}')
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_em}" id="{res_idx}_emission"]')
            properties.append(f'emission_texture = ExtResource("{res_idx}_emission")')
            res_idx += 1
    elif is_holo or (is_neon_mat and neon_col):
        properties.append("emission_enabled = true")
        final_em_col = neon_col or (0.2, 0.8, 1.0, 1.0)
        properties.append(f'emission = Color({final_em_col[0]:.6g}, {final_em_col[1]:.6g}, {final_em_col[2]:.6g}, 1)')
        em_power = floats.get("_Emission_Power", floats.get("_EmissionStrength", 2.0))
        properties.append(f'emission_energy_multiplier = {em_power:.6g}')
        if albedo_res_id and is_holo:
            properties.append(f'emission_texture = ExtResource("{albedo_res_id}")')

    # 6. Roughness & Metallic
    smooth = floats.get("_Smoothness", floats.get("_Glossiness", 0.2))
    rough = max(0.0, min(1.0, 1.0 - smooth))
    properties.append(f'roughness = {rough:.6g}')
    metallic = floats.get("_Metallic", 0.0)
    if metallic > 0.01:
        properties.append(f'metallic = {metallic:.6g}')

    if "triplanar" in mat_stem.lower() or "wall" in mat_stem.lower():
        properties.append("uv1_triplanar = true\nuv1_world_triplanar = true\nuv1_scale = Vector3(0.5, 0.5, 0.5)")

    mat_content = f"""[gd_resource type="StandardMaterial3D" load_steps={len(ext_resources) + 1} format=3]

"""
    for r in ext_resources:
        mat_content += r + "\n"
    mat_content += f"""
[resource]
resource_name = "{mat_stem}"
"""
    for p in properties:
        mat_content += p + "\n"

    return mat_content


# ==============================================================================
# 2. Project File Discovery & Texture Sanitization
# ==============================================================================
def collect_project_files(project_root: str) -> Dict[str, List[str]]:
    categories: Dict[str, List[str]] = {
        "images": [],
        "materials": [],
        "fbx": [],
        "imports": [],
        "tscn": [],
        "unitypackages": [],
        "unityscenes": [],
    }
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            low = f.lower()
            full_path = os.path.join(root, f)
            if any(low.endswith(ext) for ext in [".png", ".tga", ".jpg", ".jpeg", ".webp"]):
                categories["images"].append(full_path)
            elif low.endswith(".tres") or low.endswith(".mat") or low.endswith(".material"):
                categories["materials"].append(full_path)
            elif low.endswith(".fbx"):
                categories["fbx"].append(full_path)
            elif low.endswith(".import"):
                categories["imports"].append(full_path)
            elif low.endswith(".tscn"):
                categories["tscn"].append(full_path)
            elif low.endswith(".unitypackage") and "/addons/" not in full_path.replace("\\", "/") and "/test/" not in full_path.replace("\\", "/"):
                categories["unitypackages"].append(full_path)
            elif low.endswith(".unity") and "/test/" not in full_path.replace("\\", "/"):
                categories["unityscenes"].append(full_path)
    return categories


def save_image_safe(img: Optional[Any], target_path: str) -> None:
    if target_path.lower().endswith(".psd"):
        return
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    ext = os.path.splitext(target_path)[1].lower()

    if not HAS_PIL or img is None:
        try:
            with open(target_path, "wb") as fh:
                if ext == ".tga":
                    fh.write(TGA_1X1_BYTES)
                else:
                    fh.write(PNG_1X1_BYTES)
        except Exception:
            pass
        return

    fmt = "PNG" if ext == ".png" else ("TGA" if ext == ".tga" else "JPEG")
    try:
        img.save(target_path, format=fmt)
    except Exception:
        pass


def sanitize_and_resolve_textures(project_root: str, categorized_files: Dict[str, List[str]]) -> Tuple[int, int, int]:
    fixed_formats = aliases_created = normalized_imports = 0
    ext_to_fmt = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}

    pack_atlases: Dict[str, str] = {}
    all_atlases: List[str] = []

    for p in categorized_files.get("images", []):
        low = os.path.basename(p).lower()
        pack_dir = get_pack_root(p)
        if os.path.normpath(pack_dir) == os.path.normpath(project_root):
            continue
        if looks_like_atlas(low):
            pack_atlases.setdefault(pack_dir, p)
            all_atlases.append(p)

    global_default_atlas = next(iter(pack_atlases.values()), all_atlases[0] if all_atlases else None)

    # 1. Format check & repair
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

    # 2. Missing Texture Discovery
    referenced_textures: Set[str] = set()

    def scan_tex_refs(p: str) -> None:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            for rel in RE_RES_TEX.findall(txt):
                if not rel.lower().endswith(".psd"):
                    referenced_textures.add(rel)
        except Exception:
            pass

    scan_targets = categorized_files.get("materials", []) + categorized_files.get("tscn", [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(scan_tex_refs, scan_targets))

    for rel in referenced_textures:
        if rel.lower().endswith(".psd") or "/" not in rel.replace("res://", "", 1):
            continue
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

    # 3. Texture .import normalizer
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

    return fixed_formats, aliases_created, normalized_imports


# ==============================================================================
# 3. Deep FBX Binary Connection Graph Parser
# ==============================================================================
def read_fbx_properties(data: bytes, offset: int, num_props: int) -> Tuple[List[Any], int]:
    props = []
    curr = offset
    for _ in range(num_props):
        if curr >= len(data):
            break
        type_code = chr(data[curr])
        curr += 1
        if type_code == 'Y':
            props.append(struct.unpack("<h", data[curr:curr + 2])[0])
            curr += 2
        elif type_code == 'C':
            props.append(struct.unpack("<?", data[curr:curr + 1])[0])
            curr += 1
        elif type_code == 'I':
            props.append(struct.unpack("<i", data[curr:curr + 4])[0])
            curr += 4
        elif type_code == 'F':
            props.append(struct.unpack("<f", data[curr:curr + 4])[0])
            curr += 4
        elif type_code == 'D':
            props.append(struct.unpack("<d", data[curr:curr + 8])[0])
            curr += 8
        elif type_code == 'L':
            props.append(struct.unpack("<q", data[curr:curr + 8])[0])
            curr += 8
        elif type_code in ('R', 'S'):
            length = struct.unpack("<I", data[curr:curr + 4])[0]
            curr += 4
            val = data[curr:curr + length].decode("utf-8", errors="ignore")
            props.append(val)
            curr += length
        elif type_code in ('f', 'd', 'l', 'i', 'b'):
            arr_len, enc, comp_len = struct.unpack("<III", data[curr:curr + 12])
            curr += 12
            payload = data[curr:curr + comp_len]
            curr += comp_len
            values = None
            if enc == 1:
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    payload = b''
            if enc in (0, 1):
                fmt = {'f': 'f', 'd': 'd', 'l': 'q', 'i': 'i', 'b': 'b'}[type_code]
                size = struct.calcsize('<' + fmt)
                if len(payload) == arr_len * size:
                    values = list(struct.unpack('<' + fmt * arr_len, payload))
            props.append(values)
        else:
            break
    return props, curr


def resolve_geometry_material_record(
    geometry_id: int,
    model_id: Optional[int],
    mapping: str,
    reference: str,
    material_indices: List[int],
    model_material_ids: List[int],
) -> Dict[str, Any]:
    """Build an ID-keyed material-layer record without guessing incomplete slots."""
    authoritative = bool(
        model_id is not None and mapping in ("AllSame", "ByPolygon", "ByVertex", "ByVertice")
        and reference in ("Direct", "IndexToDirect")
    )
    material_ids: List[int] = []
    if authoritative:
        indices = material_indices
        if reference == "IndexToDirect":
            # IndexToDirect entries select entries in the direct model-slot list.
            if any(index < 0 or index >= len(model_material_ids) for index in indices):
                authoritative = False
            else:
                indices = [model_material_ids[index] for index in indices]
        else:
            if any(index < 0 for index in indices):
                authoritative = False
        if authoritative:
            # Preserve the first-occurrence order encoded by the layer array.
            material_ids = list(dict.fromkeys(indices))
    return {
        "geometry_id": geometry_id,
        "model_id": model_id,
        "mapping": mapping,
        "reference": reference,
        "material_indices": material_indices,
        "material_ids": material_ids,
        "authoritative": authoritative,
    }


def extract_geometry_material_layers(
    geometry_node: Tuple[str, List[Any], List[Any]],
) -> Dict[int, Dict[str, Any]]:
    """Extract nested LayerElementMaterial nodes keyed by Geometry ID."""
    geometry_id = geometry_node[1][0] if geometry_node[1] else None
    if geometry_id is None:
        return {}

    def descendants(
        node: Tuple[str, List[Any], List[Any]],
    ) -> Iterator[Tuple[str, List[Any], List[Any]]]:
        for child in node[2]:
            yield child
            yield from descendants(child)

    records: Dict[int, Dict[str, Any]] = {}
    for layer in descendants(geometry_node):
        if layer[0] != "LayerElementMaterial":
            continue
        mapping = ""
        reference = ""
        material_indices = None
        for property_node in descendants(layer):
            if property_node[0] == "MappingInformationType" and property_node[1]:
                mapping = str(property_node[1][0])
            elif property_node[0] == "ReferenceInformationType" and property_node[1]:
                reference = str(property_node[1][0])
            elif property_node[0] == "Materials" and property_node[1]:
                material_indices = property_node[1][0]
        if isinstance(material_indices, list):
            records[geometry_id] = {
                "geometry_id": geometry_id,
                "model_id": None,
                "mapping": mapping,
                "reference": reference,
                "material_indices": material_indices,
                "authoritative": bool(mapping and reference),
            }
    return records


def parse_fbx_graph(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()
    except Exception:
        return {}

    if not data.startswith(b"Kaydara FBX Binary"):
        return {}

    version = struct.unpack("<I", data[23:27])[0]
    is_64bit = version >= 7500

    def parse_node(offset: int):
        if is_64bit:
            if offset + 25 > len(data):
                return None, len(data)
            end_offset, num_props, prop_len, name_len = struct.unpack("<QQQB", data[offset:offset + 25])
            header_size = 25
        else:
            if offset + 13 > len(data):
                return None, len(data)
            end_offset, num_props, prop_len, name_len = struct.unpack("<IIIB", data[offset:offset + 13])
            header_size = 13
        if end_offset == 0:
            return None, offset + header_size
        name = data[offset + header_size:offset + header_size + name_len].decode("ascii", errors="ignore")
        prop_offset = offset + header_size + name_len
        props, _ = read_fbx_properties(data, prop_offset, num_props)
        child_offset = prop_offset + prop_len
        children = []
        while child_offset < end_offset:
            child, next_off = parse_node(child_offset)
            if child:
                children.append(child)
                child_offset = next_off
            else:
                break
        return (name, props, children), end_offset

    root = []
    off = 27
    while off < len(data):
        node, next_off = parse_node(off)
        if node:
            root.append(node)
            off = next_off
        else:
            break

    objects = next((n for n in root if n[0] == "Objects"), None)
    materials: Dict[int, str] = {}
    models: Dict[int, str] = {}
    geometries_by_id: Dict[int, Any] = {}
    geometry_materials_by_id: Dict[int, Dict[str, Any]] = {}
    textures: Dict[int, Tuple[str, str]] = {}
    videos: Dict[int, str] = {}
    has_skin = False
    if objects:
        for c in objects[2]:
            c_name, c_props, c_children = c
            if c_name == "Material" and len(c_props) >= 2:
                mat_id = c_props[0]
                mat_name = str(c_props[1]).split(chr(0))[0].split("::")[-1]
                materials[mat_id] = mat_name
            elif c_name == "Model" and len(c_props) >= 2:
                model_id = c_props[0]
                models[model_id] = str(c_props[1]).split(chr(0))[0].split("::")[-1]
            elif c_name == "Geometry" and c_props:
                geometry_id = c_props[0]
                geometries_by_id[geometry_id] = c
                geometry_materials_by_id.update(extract_geometry_material_layers(c))
            elif c_name == "Deformer" and len(c_props) >= 3 and c_props[2] == "Skin":
                has_skin = True
            elif c_name == "Texture" and len(c_props) >= 2:
                tex_id = c_props[0]
                tex_name = str(c_props[1]).split(chr(0))[0].split("::")[-1]
                fname = ""
                for sub in c_children:
                    if sub[0] in ("RelativeFilename", "FileName") and sub[1]:
                        fname = str(sub[1][0])
                        break
                textures[tex_id] = (tex_name, fname)
            elif c_name == "Video" and len(c_props) >= 2:
                vid_id = c_props[0]
                fname = ""
                for sub in c_children:
                    if sub[0] in ("RelativeFilename", "Filename") and sub[1]:
                        fname = str(sub[1][0])
                        break
                videos[vid_id] = fname

    connections_node = next((n for n in root if n[0] == "Connections"), None)
    mat_to_textures: Dict[int, List[str]] = {}
    texture_videos: Dict[int, List[str]] = {}
    model_material_ids: Dict[int, List[int]] = {}
    geometry_model_ids: Dict[int, int] = {}
    if connections_node:
        connections = [c[1] for c in connections_node[2] if len(c[1]) >= 3]
        # FBX stores the Video -> Texture edge separately from the
        # Texture -> Material edge, and the edges are not ordered. Resolve
        # the first edge set before resolving material texture assignments.
        for c_props in connections:
            kind, child_id, parent_id = c_props[:3]
            if kind == "OO" and parent_id in textures and child_id in videos:
                texture_videos.setdefault(parent_id, []).append(videos[child_id])
        for c_props in connections:
            kind, child_id, parent_id = c_props[:3]
            if kind in ("OO", "OP") and parent_id in materials and child_id in textures:
                tname, tfname = textures[child_id]
                linked_videos = texture_videos.get(child_id, [])
                final_path = (linked_videos[0] if linked_videos else tfname) or tname
                mat_to_textures.setdefault(parent_id, []).append(final_path)
            elif kind in ("OO", "OP") and parent_id in models and child_id in materials:
                model_material_ids.setdefault(parent_id, []).append(child_id)
            elif kind == "OO" and parent_id in models and child_id in geometries_by_id:
                geometry_model_ids[child_id] = parent_id

    model_material_ids_by_id: Dict[int, List[int]] = {}
    for geometry_id, old_record in list(geometry_materials_by_id.items()):
        model_id = geometry_model_ids.get(geometry_id)
        record = resolve_geometry_material_record(
            geometry_id,
            model_id,
            old_record.get("mapping", ""),
            old_record.get("reference", ""),
            old_record.get("material_indices", []),
            model_material_ids.get(model_id, []) if model_id is not None else [],
        )
        geometry_materials_by_id[geometry_id] = record
        if model_id is not None and record["authoritative"]:
            model_material_ids_by_id[model_id] = list(dict.fromkeys(
                model_material_ids_by_id.get(model_id, []) + record["material_ids"]
            ))

    slot_to_tex: Dict[str, List[str]] = {}
    for mid, mname in materials.items():
        slot_to_tex[mname] = mat_to_textures.get(mid, [])

    geometry_nodes_by_id = geometries_by_id
    geometries_by_id = {
        geometry_id: {
            "geometry_id": geometry_id,
            "name": str(node[1][1]).split(chr(0))[0].split("::")[-1] if len(node[1]) > 1 else "",
            "model_id": geometry_model_ids.get(geometry_id),
            "node": node,
        }
        for geometry_id, node in geometry_nodes_by_id.items()
    }

    model_names = list(models.values())
    duplicate_model_names = {name for name in model_names if model_names.count(name) > 1}
    model_materials = {
        models[model_id]: [materials[mid] for mid in material_ids if mid in materials]
        for model_id, material_ids in model_material_ids.items()
        if models.get(model_id) not in duplicate_model_names
    }

    return {
        "materials": materials,
        "models": models,
        "models_by_id": models,
        "geometries_by_id": geometries_by_id,
        "geometry_model_ids": geometry_model_ids,
        "geometry_materials_by_id": geometry_materials_by_id,
        "geometry_material_layers": geometry_materials_by_id,
        "model_material_ids_by_id": model_material_ids_by_id,
        "model_materials": model_materials,
        "ambiguous_model_names": sorted(duplicate_model_names),
        "textures": textures,
        "videos": videos,
        "mat_to_textures": slot_to_tex,
        "has_skin": has_skin
    }


# ==============================================================================
# 4. Deterministic Material Synchronization & Slot Resolution
# ==============================================================================
def parse_unity_prefab_renderer_contexts(raw_yaml: str) -> List[Dict[str, Any]]:
    """Return each Unity renderer's node name, mesh GUID, and ordered material GUIDs."""
    game_names = {
        match.group(1): name.group(1).strip()
        for match in re.finditer(
            r"--- !u!1 &(\d+)\s*\nGameObject:\s*\n(.*?)(?=^--- !u!|\Z)",
            raw_yaml, re.S | re.M
        )
        for name in [re.search(r"^\s*m_Name:\s*(.+?)\s*$", match.group(2), re.M)]
        if name
    }
    mesh_by_go = {}
    for match in re.finditer(
        r"--- !u!(?:33|137) &\d+\s*\n(?:MeshFilter|SkinnedMeshRenderer):\s*\n(.*?)(?=^--- !u!|\Z)",
        raw_yaml, re.S | re.M
    ):
        go = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)\}", match.group(1))
        mesh = re.search(r"m_Mesh:\s*\{fileID:[^,]+,\s*guid:\s*([a-f0-9]{32})", match.group(1))
        if go and mesh:
            mesh_by_go[go.group(1)] = mesh.group(1)
    contexts = []
    for match in re.finditer(
        r"--- !u!(?:23|137) &\d+\s*\n(?:MeshRenderer|SkinnedMeshRenderer):\s*\n(.*?)(?=^--- !u!|\Z)",
        raw_yaml, re.S | re.M
    ):
        block = match.group(1)
        go = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)\}", block)
        materials_block = re.search(
            r"m_Materials:\s*\n((?:\s*-\s*\{[^\n]+\}\s*\n?)+)", block
        )
        if not go:
            continue
        go_id = go.group(1)
        material_slots = []
        if materials_block:
            for item in re.findall(r"^\s*-\s*\{([^\n]+)\}", materials_block.group(1), re.M):
                guid = re.search(r"guid:\s*([a-f0-9]{32})", item)
                # Keep null Unity material entries as positional placeholders.
                material_slots.append(guid.group(1) if guid else "")
        contexts.append({
            "name": game_names.get(go_id, go_id),
            "game_object_id": go_id,
            "mesh_guid": mesh_by_go.get(go_id, ""),
            "materials": material_slots,
        })
    return contexts


def build_contextual_material_overrides(
    graph: Dict[str, Any],
    renderer_contexts: List[Dict[str, Any]],
    material_by_fbx_name: Dict[str, str],
    resource_by_material_name: Dict[str, str],
) -> Dict[str, Dict[int, str]]:
    """Map only conflicting FBX material uses to renderer-specific surfaces."""
    overrides: Dict[str, Dict[int, str]] = {}
    ambiguous_model_names = set(graph.get("ambiguous_model_names", []))
    # Contextual overrides require an explicit Geometry layer. Legacy model
    # names alone do not establish which renderer owns a material slot.
    canonical_model_materials: Dict[str, Tuple[List[int], List[str]]] = {}
    authoritative_material_orders: Dict[str, List[int]] = {}
    invalid_model_names: Set[str] = set()
    models_by_id = graph.get("models_by_id", graph.get("models", {}))
    materials_by_id = graph.get("materials", {})
    connected_by_model = graph.get("model_material_ids_by_id", {})
    geometry_layers = graph.get("geometry_materials_by_id")
    if geometry_layers is None:
        # Keep compatibility with the parser's original public alias.
        geometry_layers = graph.get("geometry_material_layers", {})

    for record in geometry_layers.values():
        if not record.get("authoritative"):
            continue
        model_id = record.get("model_id")
        model_name = models_by_id.get(model_id)
        if not model_name:
            continue
        # Refuse the whole model if any authoritative geometry declares a
        # different material order, even when its mapping is not usable for
        # generating an override. Otherwise an unsupported layer can hide a
        # real conflict while a different layer supplies the canonical order.
        declared_ids = list(record.get("material_ids", []))
        if declared_ids:
            existing_order = authoritative_material_orders.get(model_name)
            if existing_order is not None and existing_order != declared_ids:
                invalid_model_names.add(model_name)
            else:
                authoritative_material_orders[model_name] = declared_ids
        connected_material_ids = list(connected_by_model.get(model_id, []))
        mapping = record.get("mapping")
        reference = record.get("reference")
        # Test/compatibility graph records may already contain the resolved
        # ordered IDs. Treat those as authoritative, but still validate them
        # against the model connections and the other geometry records below.
        if not mapping and not reference and "material_ids" in record:
            ordered_ids = list(record.get("material_ids", []))
            if not ordered_ids or any(material_id not in connected_material_ids for material_id in ordered_ids):
                invalid_model_names.add(model_name)
                continue
        else:
            if mapping not in ("AllSame", "ByPolygon"):
                # Unsupported layers cannot establish a canonical slot order,
                # but their declared order was checked above for conflicts.
                # Let a supported layer preserve the real renderer mapping.
                continue
            indices = list(record.get("material_indices", []))
            if mapping == "AllSame" and len(indices) != 1:
                invalid_model_names.add(model_name)
                continue
            if reference == "IndexToDirect":
                if any(not isinstance(index, int) or index < 0 or index >= len(connected_material_ids) for index in indices):
                    invalid_model_names.add(model_name)
                    continue
                ordered_ids = [connected_material_ids[index] for index in indices]
            elif reference == "Direct":
                ordered_ids = indices
                if any(material_id not in connected_material_ids for material_id in ordered_ids):
                    invalid_model_names.add(model_name)
                    continue
            else:
                invalid_model_names.add(model_name)
                continue
        ordered_ids = list(dict.fromkeys(ordered_ids))
        # The authoritative IDs must agree with the sequence rebuilt from
        # layer indices and ordered model connections.
        if not ordered_ids or list(record.get("material_ids", [])) != ordered_ids:
            invalid_model_names.add(model_name)
            continue
        names = [materials_by_id[mid] for mid in ordered_ids if mid in materials_by_id]
        if len(names) != len(ordered_ids):
            invalid_model_names.add(model_name)
            continue
        existing = canonical_model_materials.get(model_name)
        if existing and existing[0] != ordered_ids:
            invalid_model_names.add(model_name)
            continue
        canonical_model_materials[model_name] = (ordered_ids, names)
    for model_name, fbx_slots in graph.get("model_materials", {}).items():
        if (
            model_name in ambiguous_model_names
            or model_name in invalid_model_names
            or model_name not in canonical_model_materials
        ):
            continue
        connected_ids, connected_names = canonical_model_materials[model_name]
        # Every FBX-wide slot must be a connected material slot. This keeps
        # renderer indices from being applied to stale or guessed materials.
        if not connected_names or any(name not in connected_names for name in fbx_slots):
            continue
        candidates = [context for context in renderer_contexts if context.get("name") == model_name]
        # Do not guess when duplicate source node names make renderer identity
        # ambiguous. A skipped contextual override preserves the existing
        # FBX-wide fallback instead of changing an unrelated renderer.
        if len(candidates) != 1:
            continue
        unity_slots = candidates[0].get("materials", [])
        for index, fbx_name in enumerate(fbx_slots):
            if index >= len(unity_slots) or index >= len(connected_ids):
                continue
            resource = resource_by_material_name.get(
                unity_slots[index],
                unity_slots[index] if unity_slots[index].startswith("res://") else "",
            )
            if resource and resource != material_by_fbx_name.get(fbx_name, ""):
                overrides.setdefault(model_name, {})[index] = resource
    return overrides


def generate_contextual_import_script(
    project_root: str,
    fbx_path: str,
    overrides: Dict[str, Dict[int, str]],
) -> str:
    """Write a post-import script for node-specific surface material overrides."""
    if not overrides:
        return ""
    fbx_stem = os.path.splitext(os.path.basename(fbx_path))[0]
    path_tag = hashlib.sha1(os.path.normpath(fbx_path).encode("utf-8")).hexdigest()[:12]
    script_dir = os.path.join(project_root, "addons", "synty_importer", "generated_material_scripts")
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, f"{fbx_stem}_{path_tag}.gd")
    lines: List[str] = [
        "@tool",
        "extends EditorScenePostImport",
        "",
        "func _post_import(scene: Node) -> Node:",
    ]
    first_node = True
    for node_name in sorted(overrides):
        safe_name = json.dumps(node_name)
        declaration = "var target_node" if first_node else "target_node"
        lines.append(f"    {declaration} = _find_unique_node(scene, {safe_name})")
        lines.append("    if target_node is MeshInstance3D:")
        for surface_index in sorted(overrides[node_name]):
            material_path = json.dumps(overrides[node_name][surface_index])
            lines.append(
                f"        target_node.set_surface_override_material({surface_index}, load({material_path}))"
            )
        first_node = False
    lines.extend([
        "    return scene",
        "",
        "func _find_unique_node(root: Node, wanted_name: String) -> Node:",
        "    var matches: Array[Node] = []",
        "    _collect_named_nodes(root, wanted_name, matches)",
        "    return matches[0] if matches.size() == 1 else null",
        "",
        "func _collect_named_nodes(",
        "        node: Node, wanted_name: String, matches: Array[Node]",
        ") -> void:",
        "    if node.name == wanted_name:",
        "        matches.append(node)",
        "    for child in node.get_children():",
        "        _collect_named_nodes(child, wanted_name, matches)",
        "",
    ])
    with open(script_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines))
    return "res://" + os.path.relpath(script_path, project_root).replace("\\", "/")


def parse_unity_prefab_material_arrays(raw_yaml: str) -> Optional[List[str]]:
    """Return one safe renderer array, or None when renderer arrays differ."""
    arrays = []
    for block in re.finditer(
        r"m_Materials:\s*\n((?:\s*-\s*\{fileID:[^\}]+\}\s*\n?)+)",
        raw_yaml,
    ):
        slots = []
        for item in re.findall(r"^\s*-\s*\{([^\n]+)\}", block.group(1), re.M):
            guid = re.search(r"guid:\s*([a-f0-9]{32})", item)
            slots.append(guid.group(1) if guid else "")
        arrays.append(slots)
    if not arrays:
        return []
    first = arrays[0]
    return first if all(slots == first for slots in arrays[1:]) else None


def synchronize_unitypackage_materials(
    project_root: str,
    categorized_files: Dict[str, List[str]]
) -> Tuple[int, Dict[str, Any]]:
    updated_mats = 0
    pack_prefab_mats: Dict[str, Any] = {}
    pack_prefab_contexts: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def add_prefab_mats(pack_key: str, model_key: str, mats: List[str]) -> None:
        norm_pack = os.path.normpath(pack_key)
        pack_dict = pack_prefab_mats.setdefault(norm_pack, {})
        clean_mats = [m if m.endswith(".tres") else m + ".tres" for m in mats]
        existing = pack_dict.setdefault(model_key, [])
        # Preserve renderer-slot duplicates: FBX material slots are positional.
        # Repeated calls for the same prefab/mesh must not concatenate copies.
        if not existing:
            existing.extend(clean_mats)
        elif existing != clean_mats:
            return

    # Build global guid_to_path from all packages
    global_guid_to_path: Dict[str, str] = {}
    for pkg in categorized_files.get("unitypackages", []):
        pkg_guids, _ = read_unitypackage_data(pkg)
        global_guid_to_path.update(pkg_guids)

    # Build pack_atlases and pack_images_by_stem
    pack_atlases: Dict[str, str] = {}
    all_atlases: List[str] = []
    pack_images_by_stem: Dict[Tuple[str, str], str] = {}
    for p in categorized_files.get("images", []):
        low = os.path.basename(p).lower()
        p_dir = os.path.normpath(get_pack_root(p))
        if p_dir == os.path.normpath(project_root):
            continue
        stem = os.path.splitext(low)[0]
        norm_stem = normalize_tex_stem(stem)
        pack_images_by_stem[(p_dir, stem)] = p
        pack_images_by_stem[(p_dir, norm_stem)] = p
        if looks_like_atlas(low):
            pack_atlases.setdefault(p_dir, p)
            all_atlases.append(p)
    global_default_atlas = next(iter(pack_atlases.values()), all_atlases[0] if all_atlases else None)

    # 1. Parse Unity Packages
    for pkg in categorized_files.get("unitypackages", []):
        guid_to_path, guid_to_asset = read_unitypackage_data(pkg)
        if not guid_to_path:
            continue

        mat_guid_to_res: Dict[str, str] = {}
        for guid, rel_path in guid_to_path.items():
            if rel_path.endswith(".mat") and guid in guid_to_asset:
                raw_yaml = guid_to_asset[guid].decode("utf-8", errors="ignore")
                parsed = parse_unity_mat_yaml(raw_yaml, global_guid_to_path)
                mat_stem = os.path.splitext(os.path.basename(rel_path))[0]
                pack_dir = get_pack_root(os.path.normpath(os.path.join(project_root, rel_path)))

                target_mat = os.path.join(project_root, rel_path + ".tres")
                if not os.path.exists(os.path.join(project_root, rel_path)):
                    rel_clean = rel_path.replace("\\", "/").lstrip("/")
                    parts = rel_clean.split("/")
                    mat_idx = -1
                    for idx, pt in enumerate(parts):
                        if pt.lower() == "materials":
                            mat_idx = idx
                            break
                    if mat_idx != -1:
                        mat_sub = "/".join(parts[mat_idx:])
                        target_mat = os.path.join(pack_dir, mat_sub + ".tres")

                res_p = "res://" + os.path.relpath(target_mat, project_root).replace("\\", "/")
                mat_guid_to_res[guid] = res_p

                atlas_fallback = pack_atlases.get(pack_dir) or global_default_atlas
                mat_content = generate_godot_material_from_unity(mat_stem, parsed, project_root, pack_dir, default_atlas=atlas_fallback, image_lookup=pack_images_by_stem)
                os.makedirs(os.path.dirname(target_mat), exist_ok=True)
                try:
                    with open(target_mat, "w", encoding="utf-8") as out:
                        out.write(mat_content)
                    if target_mat not in categorized_files["materials"]:
                        categorized_files["materials"].append(target_mat)
                    updated_mats += 1
                except Exception as exc:
                    fail_warn(f"Failed to write material {target_mat}: {exc}")

            elif rel_path.endswith(".prefab") and guid in guid_to_asset:
                raw_yaml = guid_to_asset[guid].decode("utf-8", errors="ignore")
                pack_dir = get_pack_root(os.path.normpath(os.path.join(project_root, rel_path)))

                prefab_guids = parse_unity_prefab_material_arrays(raw_yaml)
                mat_paths = [] if prefab_guids is None else []
                for mg in prefab_guids or []:
                    res_p = mat_guid_to_res.get(mg)
                    if not res_p:
                        mp = guid_to_path.get(mg)
                        if mp and mp.endswith(".mat"):
                            res_p = "res://" + mp.replace("\\", "/") + ".tres"
                    clean_p = res_p if res_p and res_p.endswith(".tres") else (res_p + ".tres" if res_p else "")
                    mat_paths.append(clean_p)

                if prefab_guids is None:
                    property_blocks = []
                else:
                    property_blocks = re.finditer(r"propertyPath:\s*m_Materials\.Array\.data\[(\d+)\]\s*\n\s*value:\s*\n\s*objectReference:\s*\{fileID:[^,]+,\s*guid:\s*([a-f0-9]{32})", raw_yaml)
                for block in property_blocks:
                    idx, mg = int(block.group(1)), block.group(2)
                    res_p = mat_guid_to_res.get(mg)
                    if not res_p:
                        mp = guid_to_path.get(mg)
                        if mp and mp.endswith(".mat"):
                            res_p = "res://" + mp.replace("\\", "/") + ".tres"
                    if res_p:
                        clean_p = res_p if res_p.endswith(".tres") else res_p + ".tres"
                        while len(mat_paths) <= idx:
                            # An indexed override does not imply that the
                            # intervening Unity slots contain this material.
                            # Preserve positional holes until the override is
                            # applied at its explicit index.
                            mat_paths.append("")
                        mat_paths[idx] = clean_p

                if mat_paths:
                    mesh_guids = re.findall(r"m_Mesh:\s*\{fileID:[^,]+,\s*guid:\s*([a-f0-9]{32})", raw_yaml)
                    for msh in mesh_guids:
                        mesh_path = guid_to_path.get(msh, "")
                        if mesh_path:
                            model_stem = os.path.splitext(os.path.basename(mesh_path))[0].lower()
                            add_prefab_mats(pack_dir, model_stem, mat_paths)
                    prefab_stem = os.path.splitext(os.path.basename(rel_path))[0].lower()
                    add_prefab_mats(pack_dir, prefab_stem, mat_paths)
                    contexts = []
                    for context in parse_unity_prefab_renderer_contexts(raw_yaml):
                        context = dict(context)
                        resolved_context_materials = []
                        for material_guid in context["materials"]:
                            material_path = mat_guid_to_res.get(material_guid, "") if material_guid else ""
                            if not material_path and material_guid:
                                material_rel_path = guid_to_path.get(material_guid, "")
                                if material_rel_path.endswith(".mat"):
                                    material_path = "res://" + material_rel_path.replace("\\", "/") + ".tres"
                            # Preserve Unity's array indices, including null slots.
                            resolved_context_materials.append(material_path)
                        context["materials"] = resolved_context_materials
                        contexts.append(context)
                    if contexts:
                        context_map = pack_prefab_contexts.setdefault(os.path.normpath(pack_dir), {})
                        for context in contexts:
                            context["prefab_stem"] = prefab_stem
                            context["prefab_path"] = rel_path.replace("\\", "/")
                        context_map.setdefault(prefab_stem, []).extend(contexts)
                        # A Unity prefab name and its FBX filename do not have
                        # to match. Index the same renderer contexts by the
                        # source mesh stem as a stable cross-format identity.
                        for context in contexts:
                            mesh_rel_path = guid_to_path.get(context.get("mesh_guid", ""), "")
                            mesh_stem = os.path.splitext(os.path.basename(mesh_rel_path))[0].lower()
                            if mesh_stem and mesh_stem != prefab_stem:
                                context_map.setdefault(mesh_stem, []).append(context)

    # 2. Synchronize standalone on-disk .mat files
    for mpath in categorized_files.get("materials", []):
        if mpath.endswith(".mat"):
            target_mat = mpath + ".tres"
            pack_dir = get_pack_root(mpath)
            mat_stem = os.path.splitext(os.path.basename(mpath))[0]
            try:
                with open(mpath, "r", encoding="utf-8", errors="ignore") as fh:
                    raw_yaml = fh.read()
                parsed = parse_unity_mat_yaml(raw_yaml, global_guid_to_path)
                atlas_fallback = pack_atlases.get(pack_dir) or global_default_atlas
                mat_content = generate_godot_material_from_unity(mat_stem, parsed, project_root, pack_dir, default_atlas=atlas_fallback, image_lookup=pack_images_by_stem)
                os.makedirs(os.path.dirname(target_mat), exist_ok=True)
                with open(target_mat, "w", encoding="utf-8") as out:
                    out.write(mat_content)
                if target_mat not in categorized_files["materials"]:
                    categorized_files["materials"].append(target_mat)
                updated_mats += 1
            except Exception:
                pass

    # 3. Synchronize .prefab.tscn overrides
    for p in categorized_files.get("tscn", []):
        if p.endswith(".prefab.tscn"):
            pack_dir = get_pack_root(os.path.normpath(p))
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
                ext_mats = {}
                for line in txt.splitlines():
                    if line.startswith("[ext_resource") and "Material" in line:
                        id_m = re.search(r'id="([^"]+)"', line)
                        path_m = re.search(r'path="([^"]+)"', line)
                        if id_m and path_m:
                            ext_mats[id_m.group(1)] = path_m.group(1)
                mesh_matches = re.findall(r'path="res://[^"]*?/([a-zA-Z0-9_-]+)\.(?:mesh|fbx)"', txt)
                p_stem = os.path.basename(p).replace(".prefab.tscn", "").lower()
                if ext_mats:
                    m_list = [m if m.endswith(".tres") else m + ".tres" for m in ext_mats.values()]
                    add_prefab_mats(pack_dir, p_stem, m_list)
                    for msh in mesh_matches:
                        add_prefab_mats(pack_dir, msh.lower(), m_list)
            except Exception:
                pass

    # 4. Cleanup stale flat materials in root Materials/ folder if nested version exists
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        if os.path.basename(root).lower() == "materials" and len(dirs) > 0:
            for f in files:
                if f.endswith(".mat.tres"):
                    for sub in dirs:
                        nested_cand = os.path.join(root, sub, f)
                        nested_mat = os.path.join(root, sub, f[:-5])
                        if os.path.exists(nested_cand) or os.path.exists(nested_mat):
                            flat_file = os.path.join(root, f)
                            try:
                                os.remove(flat_file)
                                if flat_file in categorized_files["materials"]:
                                    categorized_files["materials"].remove(flat_file)
                            except Exception:
                                pass
                            break

    pack_prefab_mats["__renderer_contexts__"] = pack_prefab_contexts
    return updated_mats, pack_prefab_mats


def resolve_slot_material(
    slot: str,
    connected_tex: List[str],
    mats: Dict[str, str],
    tex_to_mat: Dict[str, str],
    default_atlas: str,
    prefab_mats: Optional[List[str]] = None,
    slot_index: int = 0
) -> str:
    res = ""
    if prefab_mats is not None:
        # Unity's renderer material array is positional. Preserve duplicate
        # entries so FBX slots and Unity submesh slots stay aligned.
        res = prefab_mats[slot_index] if slot_index < len(prefab_mats) else ""
    else:
        skip_sfx = ("_normal", "_normals", "_n", "_emissive", "_emission", "emissive", "emission", "_occlusion", "_ao", "_mask", "_alpha")
        diffuse_candidates = [t for t in connected_tex if not any(sfx in os.path.basename(t).lower() for sfx in skip_sfx)]
        search_order = diffuse_candidates if diffuse_candidates else connected_tex

        for tex in search_order:
            low = os.path.basename(tex).lower()
            stem = normalize_tex_stem(low)
            if stem in tex_to_mat:
                res = tex_to_mat[stem]
                break

        if not res:
            s_low = slot.lower()
            if s_low in mats:
                res = mats[s_low]
            else:
                norm_slot = RE_CLEAN_PREFIX.sub("", s_low)
                norm_slot = RE_CLEAN_SUFFIX.sub("", norm_slot)
                if norm_slot in mats:
                    res = mats[norm_slot]
                elif normalize_tex_stem(norm_slot) in tex_to_mat:
                    res = tex_to_mat[normalize_tex_stem(norm_slot)]
                elif normalize_tex_stem(norm_slot) in mats:
                    res = mats[normalize_tex_stem(norm_slot)]
                elif any(term in s_low for term in ["wall", "brick", "building"]):
                    num_m = RE_NUM_EXT.search(slot)
                    if num_m:
                        target_key = f"wall_{num_m.group(1).zfill(2)}"
                        for k in [f"{target_key}_a", f"{target_key}_b", target_key]:
                            if k in mats:
                                res = mats[k]
                                break

    if not res:
        res = default_atlas

    if res and res.endswith(".mat"):
        res += ".tres"

    return res


def map_all_fbx_materials(
    project_root: str,
    categorized_files: Dict[str, List[str]],
    pack_prefab_mats: Dict[str, Dict[str, List[str]]]
) -> Tuple[int, int]:
    pack_materials: Dict[str, Dict[str, str]] = {}
    all_materials: Dict[str, str] = {}
    pack_tex_to_mat: Dict[str, Dict[str, str]] = {}
    global_tex_to_mat: Dict[str, str] = {}

    for p in categorized_files.get("materials", []):
        target_p = p + ".tres" if p.endswith(".mat") else p
        stem = os.path.basename(p).replace(".mat.tres", "").replace(".tres", "").replace(".mat", "").lower()
        norm_mat_stem = normalize_tex_stem(stem)
        rel = "res://" + os.path.relpath(target_p, project_root).replace("\\", "/")
        pack_dir = os.path.normpath(get_pack_root(p))

        p_mats = pack_materials.setdefault(pack_dir, {})
        p_mats[stem] = rel
        p_mats[norm_mat_stem] = rel
        all_materials[stem] = rel
        all_materials[norm_mat_stem] = rel

        p_tex = pack_tex_to_mat.setdefault(pack_dir, {})
        p_tex[stem] = rel
        p_tex[norm_mat_stem] = rel
        global_tex_to_mat[stem] = rel
        global_tex_to_mat[norm_mat_stem] = rel

    for p in categorized_files.get("images", []):
        base = os.path.basename(p)
        stem = os.path.splitext(base)[0].lower()
        norm_stem = normalize_tex_stem(stem)
        pack_dir = os.path.normpath(get_pack_root(p))
        p_mats = pack_materials.get(pack_dir, {})
        mat_path = p_mats.get(stem) or p_mats.get(norm_stem) or all_materials.get(stem) or all_materials.get(norm_stem)
        if mat_path:
            if mat_path.endswith(".mat"):
                mat_path += ".tres"
            pack_tex_to_mat.setdefault(pack_dir, {})[stem] = mat_path
            pack_tex_to_mat.setdefault(pack_dir, {})[norm_stem] = mat_path
            global_tex_to_mat[stem] = mat_path
            global_tex_to_mat[norm_stem] = mat_path

    total_models = total_slots = 0

    def process_fbx(fbx_path: str) -> Tuple[int, int]:
        imp_path = fbx_path + ".import"
        pack_dir = os.path.normpath(get_pack_root(fbx_path))
        model_stem = os.path.splitext(os.path.basename(fbx_path))[0].lower()

        p_mats = pack_materials.get(pack_dir, all_materials)
        p_tex = pack_tex_to_mat.get(pack_dir, global_tex_to_mat)

        pref_mats = pack_prefab_mats.get(pack_dir, {}).get(model_stem)
        if not pref_mats:
            for p_key, p_dict in pack_prefab_mats.items():
                if p_key == "__renderer_contexts__":
                    continue
                if model_stem in p_dict:
                    pref_mats = p_dict[model_stem]
                    break

        default_atlas = ""
        for k, v in p_mats.items():
            if looks_like_atlas(k):
                default_atlas = v
                break
        if not default_atlas:
            default_atlas = next(iter(p_mats.values()), next(iter(all_materials.values()), ""))

        content = ""
        if os.path.exists(imp_path):
            with open(imp_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            content = re.sub(r'("use_external/path":\s*"res://[^"]+?\.mat)"', r'\1.tres"', content)
        else:
            rel_source = "res://" + os.path.relpath(fbx_path, project_root).replace("\\", "/")
            base_f = os.path.basename(fbx_path)
            content = f"""[remap]

importer="scene"
importer_version=1
type="PackedScene"

[deps]

source_file="{rel_source}"
dest_files=["res://.godot/imported/{base_f}.scn"]

[params]

nodes/root_type=""
nodes/root_name=""
nodes/root_script=null
nodes/apply_root_scale=true
nodes/root_scale=1.0
nodes/import_as_skeleton_bones=false
nodes/use_name_suffixes=true
nodes/use_node_type_suffixes=true
meshes/ensure_tangents=true
meshes/generate_lods=true
meshes/create_shadow_meshes=true
meshes/light_baking=1
meshes/lightmap_texel_size=0.2
meshes/force_disable_compression=false
skins/use_named_skins=true
animation/import=true
animation/fps=30
animation/trimming=true
animation/remove_immutable_tracks=true
animation/import_rest_as_RESET=false
import_script/path=""
materials/extract=0
materials/extract_format=0
materials/extract_path=""
_subresources={{
"materials": {{}}
}}
fbx/importer=0
fbx/allow_geometry_helper_nodes=false
fbx/embedded_image_handling=0
fbx/naming_version=1
"""

        if "importer=\"scene\"" not in content and "type=\"PackedScene\"" not in content:
            return 0, 0

        g = parse_fbx_graph(fbx_path)
        slots = list(g.get("materials", {}).values())
        if not slots:
            slots_match = re.findall(r'"([^"]+)":\s*\{\s*"use_external/enabled"', content)
            slots = slots_match if slots_match else ["Default_Material"]

        mat_dict: Dict[str, Dict[str, Any]] = {}
        for idx, s in enumerate(slots):
            conn_tex = g.get("mat_to_textures", {}).get(s, [])
            res_mat = resolve_slot_material(
                s, conn_tex, p_mats, p_tex, default_atlas,
                prefab_mats=pref_mats, slot_index=idx
            )
            if res_mat:
                mat_dict[s] = {
                    "use_external/enabled": True,
                    "use_external/path": res_mat
                }

        # Keep the existing FBX-wide mapping as the default.  Only add a
        # post-import node/surface override when Unity proves that a shared
        # FBX material is assigned differently on a specific renderer.
        contextual_script = ""
        renderer_contexts = pack_prefab_mats.get("__renderer_contexts__", {}).get(pack_dir, {}).get(model_stem, [])
        if renderer_contexts:
            # A source mesh can be reused by multiple Unity prefabs. Do not
            # merge their renderer assignments into one FBX import. The
            # contextual path is safe only when exactly one prefab identity is
            # represented by the candidate contexts.
            prefab_ids = {
                context.get("prefab_path") or context.get("prefab_stem", "")
                for context in renderer_contexts
            }
            if len(prefab_ids - {""}) > 1:
                renderer_contexts = []
        if renderer_contexts and g.get("model_materials"):
            fallback_materials = {name: entry["use_external/path"] for name, entry in mat_dict.items()}
            material_resources = {
                path: path
                for context in renderer_contexts
                for path in context.get("materials", [])
                if path
            }
            contextual_overrides = build_contextual_material_overrides(
                g, renderer_contexts, fallback_materials, material_resources
            )
            contextual_script = generate_contextual_import_script(
                project_root, fbx_path, contextual_overrides
            )
            existing_script = re.search(r'import_script/path="([^"]*)"', content)
            if existing_script and existing_script.group(1) and "generated_material_scripts/" not in existing_script.group(1):
                # Do not overwrite a user-authored import script. The
                # contextual assignment is optional and must not replace
                # unrelated importer behavior.
                contextual_script = ""

        nodes_match = re.search(r'("nodes":\s*\{[\s\S]*?\n\s*\})', content)
        nodes_block = nodes_match.group(1) if nodes_match else None
        meshes_match = re.search(r'("meshes":\s*\{[\s\S]*?\n\s*\})', content)
        meshes_block = meshes_match.group(1) if meshes_match else None
        skins_match = re.search(r'("skins":\s*\{[\s\S]*?\n\s*\})', content)
        skins_block = skins_match.group(1) if skins_match else None

        if not meshes_block and g.get("has_skin"):
            fbx_base = os.path.splitext(os.path.basename(fbx_path))[0]
            ext_dir = os.path.join(os.path.dirname(fbx_path), "extracted")
            os.makedirs(ext_dir, exist_ok=True)
            rel_ext_dir = "res://" + os.path.relpath(ext_dir, project_root).replace("\\", "/")
            meshes_block = f'"meshes": {{\n"PATH:{fbx_base}": {{\n"save_to_file/enabled": true,\n"save_to_file/path": "{rel_ext_dir}/{fbx_base}.{fbx_base}.mesh"\n}}\n}}'
            skins_block = f'"skins": {{\n"PATH:{fbx_base}": {{\n"save_to_file/enabled": true,\n"save_to_file/path": "{rel_ext_dir}/{fbx_base}.{fbx_base}.skin.tres"\n}}\n}}'

        sub_json_lines = ['_subresources={\n"materials": {']
        for i, (k, v) in enumerate(mat_dict.items()):
            comma = "," if i < len(mat_dict) - 1 else ""
            sub_json_lines.append(f'"{k}": {{\n"use_external/enabled": true,\n"use_external/path": "{v["use_external/path"]}"\n}}{comma}')
        sub_json_lines.append("}")
        if nodes_block:
            sub_json_lines.append(",\n" + nodes_block)
        if meshes_block:
            sub_json_lines.append(",\n" + meshes_block)
        if skins_block:
            sub_json_lines.append(",\n" + skins_block)
        sub_json_lines.append("\n}")
        new_sub = "\n".join(sub_json_lines)

        if "_subresources={" in content:
            content = re.sub(r"_subresources=\{[\s\S]*?(?=\n(?:fbx/|\[|$))", new_sub, content)
        else:
            content += "\n" + new_sub + "\n"

        if "fbx/embedded_image_handling" not in content:
            params_idx = content.find("[params]")
            if params_idx != -1:
                content = content[:params_idx + 8] + "\nfbx/importer=0\nfbx/embedded_image_handling=0\nmaterials/extract=0" + content[params_idx + 8:]
            else:
                content += "\nfbx/importer=0\nfbx/embedded_image_handling=0\nmaterials/extract=0\n"

        if contextual_script:
            script_line = f'import_script/path="{contextual_script}"'
            if re.search(r'import_script/path="[^"]*"', content):
                content = re.sub(
                    r'import_script/path="[^"]*"',
                    script_line,
                    content,
                    count=1,
                )
            else:
                params_idx = content.find("[params]")
                if params_idx != -1:
                    insert_at = content.find("\n", params_idx)
                    if insert_at == -1:
                        content += "\n" + script_line + "\n"
                    else:
                        content = content[:insert_at + 1] + script_line + "\n" + content[insert_at + 1:]
                else:
                    content += "\n[params]\n" + script_line + "\n"
        else:
            content = re.sub(r'\n?import_script/path="[^"]*"', "", content, count=1)

        with open(imp_path, "w", encoding="utf-8") as out:
            out.write(content)

        return 1, len(mat_dict)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for m_cnt, s_cnt in ex.map(process_fbx, categorized_files.get("fbx", [])):
            total_models += m_cnt
            total_slots += s_cnt

    return total_models, total_slots


# ==============================================================================
# 5. Character Rig Rectification & Selective Visibility
# ==============================================================================
def fix_character_rigs_and_visibility(categorized_files: Dict[str, List[str]]) -> Tuple[int, int]:
    fixed_skels = fixed_chars = 0
    tscn_files = categorized_files.get("tscn", [])

    def fix_tscn(p: str) -> Tuple[int, int]:
        s_fix = c_fix = 0
        def _modify(txt: str) -> Optional[str]:
            nonlocal s_fix, c_fix
            changed = False
            if 'type="GeneralSkeleton"' in txt or 'type="Skeleton"' in txt:
                txt = re.sub(r'type="(?:GeneralSkeleton|Skeleton)"', 'type="Skeleton3D"', txt)
                s_fix = 1
                changed = True
            if "Character" in p or "SK_" in p:
                parts_seen = set()
                new_lines = []
                for line in txt.splitlines():
                    if line.startswith("[node name="):
                        m = re.search(r'name="([^"]+)"', line)
                        if m:
                            name = m.group(1)
                            prefix = name.split("_")[0] if "_" in name else name
                            if prefix in parts_seen:
                                line += "\nvisible = false"
                                c_fix = 1
                                changed = True
                            else:
                                parts_seen.add(prefix)
                    new_lines.append(line)
                txt = "\n".join(new_lines)
            return txt if changed else None

        if transform_file(p, _modify):
            return s_fix, c_fix
        return 0, 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for s, c in ex.map(fix_tscn, tscn_files):
            fixed_skels += s
            fixed_chars += c

    return fixed_skels, fixed_chars


# ==============================================================================
# 6. UID Synchronization Engine
# ==============================================================================
def synchronize_uids(project_root: str, categorized_files: Dict[str, List[str]]) -> Tuple[int, int]:
    path_to_uid: Dict[str, str] = {}
    uid_to_path: Dict[str, str] = {}
    scan_targets = categorized_files.get("materials", []) + categorized_files.get("imports", []) + categorized_files.get("tscn", [])

    def extract_uid(p: str) -> Optional[Tuple[str, str]]:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                first_chunk = fh.read(1024)
            uid_m = RE_EXT_UID.search(first_chunk)
            if not uid_m:
                return None
            uid = uid_m.group(1)
            if p.endswith(".import"):
                src_m = RE_SRC_FILE.search(first_chunk)
                if src_m:
                    return src_m.group(1), uid
                base = p[:-7]
                rel = "res://" + os.path.relpath(base, project_root).replace("\\", "/")
                return rel, uid
            else:
                rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
                return rel, uid
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(extract_uid, scan_targets):
            if res:
                path_to_uid[res[0]] = res[1]
                uid_to_path[res[1]] = res[0]

    synced_scenes = fixed_uids = 0

    def sync_scene(p: str) -> int:
        def _modify(txt: str) -> Optional[str]:
            changed = False
            new_lines = []
            for line in txt.splitlines():
                if line.startswith("[ext_resource") and 'uid="uid://' in line:
                    path_m = RE_EXT_PATH.search(line)
                    uid_m = RE_EXT_UID.search(line)
                    if path_m and uid_m:
                        res_p = path_m.group(1)
                        curr_uid = uid_m.group(1)
                        if res_p in path_to_uid:
                            actual_uid = path_to_uid[res_p]
                            if actual_uid != curr_uid:
                                line = line.replace(f'uid="{curr_uid}"', f'uid="{actual_uid}"')
                                changed = True
                        elif curr_uid in uid_to_path:
                            real_path = uid_to_path[curr_uid]
                            if real_path != res_p:
                                line = line.replace(f'path="{res_p}"', f'path="{real_path}"')
                                changed = True
                new_lines.append(line)
            return "\n".join(new_lines) if changed else None

        return 1 if transform_file(p, _modify) else 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        synced_scenes = sum(ex.map(sync_scene, categorized_files.get("tscn", [])))

    return synced_scenes, len(path_to_uid)


# ==============================================================================
# 7. Unity Scene (.unity -> .tscn) Compilation Engine
# ==============================================================================
def quat_mult(q1: Tuple[float, float, float, float], q2: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    )


def quat_rot_vec(q: Tuple[float, float, float, float], v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    x, y, z, w = q
    vx, vy, vz = v
    cx = y * vz - z * vy
    cy = z * vx - x * vz
    cz = x * vy - y * vx
    cx2 = cx + w * vx
    cy2 = cy + w * vy
    cz2 = cz + w * vz
    return (vx + 2.0 * (y * cz2 - z * cy2), vy + 2.0 * (z * cx2 - x * cz2), vz + 2.0 * (x * cy2 - y * cx2))


def combine_transforms(
    p_pos: Tuple[float, float, float],
    p_rot: Tuple[float, float, float, float],
    p_scale: Tuple[float, float, float],
    c_pos: Tuple[float, float, float],
    c_rot: Tuple[float, float, float, float],
    c_scale: Tuple[float, float, float]
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
    scaled_pos = (c_pos[0] * p_scale[0], c_pos[1] * p_scale[1], c_pos[2] * p_scale[2])
    rot_pos = quat_rot_vec(p_rot, scaled_pos)
    w_pos = (p_pos[0] + rot_pos[0], p_pos[1] + rot_pos[1], p_pos[2] + rot_pos[2])
    w_rot = quat_mult(p_rot, c_rot)
    w_scale = (p_scale[0] * c_scale[0], p_scale[1] * c_scale[1], p_scale[2] * c_scale[2])
    return w_pos, w_rot, w_scale


def compile_unity_scenes(project_root: str, categorized_files: Dict[str, List[str]]) -> int:
    unity_scenes = categorized_files.get("unityscenes", [])
    if not unity_scenes:
        return 0

    # 1. Map guid to path from packages
    guid_to_path: Dict[str, str] = {}
    for pkg in categorized_files.get("unitypackages", []):
        pkg_guids, _ = read_unitypackage_data(pkg)
        guid_to_path.update(pkg_guids)

    # 2. Map FBX stem to res:// path (per-pack first, then global fallback)
    fbx_by_stem: Dict[str, str] = {}
    fbx_by_pack_stem: Dict[Tuple[str, str], str] = {}
    for p in categorized_files.get("fbx", []):
        stem = os.path.splitext(os.path.basename(p))[0].lower()
        rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
        fbx_by_stem[stem] = rel
        pack_key = os.path.basename(os.path.normpath(get_pack_root(p)).rstrip("\\/")).lower()
        fbx_by_pack_stem[(pack_key, stem)] = rel

    def resolve_model_res(prefab_rel_path: str) -> Optional[str]:
        stem = os.path.splitext(os.path.basename(prefab_rel_path))[0].lower()
        if stem == "sm_prop_firdge_01":
            stem = "sm_prop_fridge_01"
        norm_pdir = os.path.normpath(get_pack_root(os.path.join(project_root, prefab_rel_path)))
        pack_key = os.path.basename(norm_pdir.rstrip("\\/")).lower()
        return fbx_by_pack_stem.get((pack_key, stem)) or fbx_by_stem.get(stem)

    compiled = 0

    for uscene in unity_scenes:
        out_tscn = os.path.splitext(uscene)[0] + ".tscn"
        try:
            with open(uscene, "r", encoding="utf-8", errors="ignore") as fh:
                raw = fh.read()

            # Parse all scene Transforms (per-block: Unity YAML field order varies)
            transforms: Dict[str, Dict[str, Any]] = {}
            for bm in re.finditer(r"--- !u!4 &(\d+)\s*\nTransform:\s*\n(.*?)(?=--- !u!|\Z)", raw, re.S):
                fid = bm.group(1)
                tb = bm.group(2)
                go_m = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)\}", tb)
                pos_m = re.search(r"m_LocalPosition:\s*\{x:\s*([-\d.eE]+),\s*y:\s*([-\d.eE]+),\s*z:\s*([-\d.eE]+)\}", tb)
                rot_m = re.search(r"m_LocalRotation:\s*\{x:\s*([-\d.eE]+),\s*y:\s*([-\d.eE]+),\s*z:\s*([-\d.eE]+),\s*w:\s*([-\d.eE]+)\}", tb)
                scale_m = re.search(r"m_LocalScale:\s*\{x:\s*([-\d.eE]+),\s*y:\s*([-\d.eE]+),\s*z:\s*([-\d.eE]+)\}", tb)
                father_m = re.search(r"m_Father:\s*\{fileID:\s*(\d+)\}", tb)
                if not (go_m and pos_m and rot_m and scale_m):
                    continue
                transforms[fid] = {
                    "goid": go_m.group(1),
                    "pos": (float(pos_m.group(1)), float(pos_m.group(2)), float(pos_m.group(3))),
                    "rot": (float(rot_m.group(1)), float(rot_m.group(2)), float(rot_m.group(3)), float(rot_m.group(4))),
                    "scale": (float(scale_m.group(1)), float(scale_m.group(2)), float(scale_m.group(3))),
                    "father": father_m.group(1) if father_m else "0",
                }

            def get_world_transform(tf_id: str, visited: Optional[Set[str]] = None) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
                if visited is None:
                    visited = set()
                if tf_id not in transforms or tf_id == "0" or tf_id in visited:
                    return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)
                visited.add(tf_id)
                t = transforms[tf_id]
                if t["father"] == "0" or t["father"] == tf_id or t["father"] not in transforms:
                    return t["pos"], t["rot"], t["scale"]
                p_pos, p_rot, p_scale = get_world_transform(t["father"], visited)
                return combine_transforms(p_pos, p_rot, p_scale, t["pos"], t["rot"], t["scale"])

            # Stripped prefab-root Transform records are serialized after the
            # corresponding PrefabInstance block. Index them globally before
            # splitting blocks so root overrides can be matched reliably.
            stripped_roots: Dict[str, List[str]] = {}
            for stripped_m in re.finditer(
                r"--- !u!4 &[^\n]+ stripped\nTransform:\n"
                r"\s*m_CorrespondingSourceObject:\s*\{fileID:\s*([^,}]+).*?\n"
                r"\s*m_PrefabInstance:\s*\{fileID:\s*(\d+)\}",
                raw,
                re.S,
            ):
                stripped_roots.setdefault(stripped_m.group(2), []).append(stripped_m.group(1).strip())

            blocks = raw.split("--- !u!1001 &")
            if len(blocks) <= 1:
                continue

            scene_name = os.path.splitext(os.path.basename(uscene))[0]
            ext_resources: Dict[str, str] = {}
            nodes_data = []

            for idx, b in enumerate(blocks[1:]):
                guid_m = re.search(r"guid:\s*([a-f0-9]{32})", b)
                if not guid_m:
                    continue
                guid = guid_m.group(1)
                prefab_path = guid_to_path.get(guid, "")
                stem = os.path.splitext(os.path.basename(prefab_path))[0].lower()
                fbx_res = resolve_model_res(prefab_path)
                if not fbx_res:
                    continue

                if fbx_res not in ext_resources:
                    ext_resources[fbx_res] = f"{len(ext_resources) + 1}_{stem}"
                res_id = ext_resources[fbx_res]

                parent_m = re.search(r"m_TransformParent:\s*\{fileID:\s*(\d+)", b)
                p_tf_id = parent_m.group(1) if parent_m else "0"

                pos = {"x": 0.0, "y": 0.0, "z": 0.0}
                rot = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
                scale = {"x": 1.0, "y": 1.0, "z": 1.0}

                lines = b.splitlines()
                # Unity writes overrides for the prefab root and nested
                # objects in one list. The stripped Transform record identifies
                # the actual prefab-root source file ID.
                instance_id = lines[0].strip() if lines else ""
                # Each override property repeats its target record, so
                # accumulate paths by target ID before selecting the prefab
                # root. Multiple stripped transforms can share one instance
                # ID; prefer the candidate carrying m_RootOrder, which Unity
                # serializes on the instance root, and never overwrite it.
                target_paths_by_id: Dict[str, Set[str]] = {}
                current_target = None
                for line in lines:
                    target_m = re.search(r"- target:\s*\{fileID:\s*([^,}]+)", line)
                    if target_m:
                        current_target = target_m.group(1).strip()
                    path_m = re.search(r"propertyPath:\s*(\S+)", line)
                    if current_target and path_m:
                        target_paths_by_id.setdefault(current_target, set()).add(path_m.group(1))

                root_source_id = None
                stripped_candidates = stripped_roots.get(instance_id, [])
                transform_paths = {"m_LocalPosition.x", "m_LocalRotation.w"}
                for candidate_id in stripped_candidates:
                    candidate_paths = target_paths_by_id.get(candidate_id, set())
                    if "m_RootOrder" in candidate_paths and transform_paths.issubset(candidate_paths):
                        root_source_id = candidate_id
                        break
                if root_source_id is None:
                    for candidate_id in stripped_candidates:
                        if transform_paths.issubset(target_paths_by_id.get(candidate_id, set())):
                            root_source_id = candidate_id
                            break
                if root_source_id is None:
                    for candidate_id, candidate_paths in target_paths_by_id.items():
                        if transform_paths.issubset(candidate_paths):
                            root_source_id = candidate_id
                            break
                current_target = None
                for i, line in enumerate(lines):
                    target_m = re.search(r"- target:\s*\{fileID:\s*([^,}]+)", line)
                    if target_m:
                        current_target = target_m.group(1).strip()
                    if root_source_id is None or current_target != root_source_id:
                        continue
                    if "propertyPath: m_LocalPosition." in line:
                        axis = line.strip()[-1]
                        for off in (1, 2):
                            if i + off < len(lines) and "value:" in lines[i + off]:
                                try:
                                    pos[axis] = float(lines[i + off].split(":")[-1])
                                except ValueError:
                                    pass
                                break
                    elif "propertyPath: m_LocalRotation." in line:
                        axis = line.strip()[-1]
                        for off in (1, 2):
                            if i + off < len(lines) and "value:" in lines[i + off]:
                                try:
                                    rot[axis] = float(lines[i + off].split(":")[-1])
                                except ValueError:
                                    pass
                                break
                    elif "propertyPath: m_LocalScale." in line:
                        axis = line.strip()[-1]
                        for off in (1, 2):
                            if i + off < len(lines) and "value:" in lines[i + off]:
                                try:
                                    scale[axis] = float(lines[i + off].split(":")[-1])
                                except ValueError:
                                    pass
                                break

                if p_tf_id != "0" and p_tf_id in transforms:
                    p_pos, p_rot, p_scale = get_world_transform(p_tf_id)
                    w_pos, w_rot, w_scale = combine_transforms(
                        p_pos, p_rot, p_scale,
                        (pos["x"], pos["y"], pos["z"]),
                        (rot["x"], rot["y"], rot["z"], rot["w"]),
                        (scale["x"], scale["y"], scale["z"])
                    )
                else:
                    w_pos = (pos["x"], pos["y"], pos["z"])
                    w_rot = (rot["x"], rot["y"], rot["z"], rot["w"])
                    w_scale = (scale["x"], scale["y"], scale["z"])

                godot_pos, godot_rot = unity_to_godot_transform(w_pos, w_rot)
                gx, gy, gz = godot_pos
                qx, qy, qz, qw = godot_rot

                basis = quat_to_basis(qx, qy, qz, qw, w_scale[0], w_scale[1], w_scale[2])
                t_str = f"Transform3D({basis[0]:.6g}, {basis[1]:.6g}, {basis[2]:.6g}, {basis[3]:.6g}, {basis[4]:.6g}, {basis[5]:.6g}, {basis[6]:.6g}, {basis[7]:.6g}, {basis[8]:.6g}, {gx:.6g}, {gy:.6g}, {gz:.6g})"
                node_name = f"{os.path.splitext(os.path.basename(prefab_path))[0]}_{idx + 1}"
                is_active = re.search(r"propertyPath: m_IsActive\s*\n\s*value: (\d)", b)
                extra = ["visible = false"] if (is_active and is_active.group(1) == "0") else []
                nodes_data.append((node_name, res_id, t_str, extra))

            if not nodes_data:
                continue

            # --- Lighting & Environment pass ---
            sub_resources: List[str] = []
            load_steps = len(ext_resources) + 1

            ambient_m = re.search(
                r"m_AmbientSkyColor:\s*\{r:\s*([\d.eE+-]+),\s*g:\s*([\d.eE+-]+),\s*b:\s*([\d.eE+-]+)",
                raw
            )
            equator_m = re.search(
                r"m_AmbientEquatorColor:\s*\{r:\s*([\d.eE+-]+),\s*g:\s*([\d.eE+-]+),\s*b:\s*([\d.eE+-]+)",
                raw
            )
            ground_m = re.search(
                r"m_AmbientGroundColor:\s*\{r:\s*([\d.eE+-]+),\s*g:\s*([\d.eE+-]+),\s*b:\s*([\d.eE+-]+)",
                raw
            )
            ambient_intensity_m = re.search(r"m_AmbientIntensity:\s*([\d.eE+-]+)", raw)
            ambient_mode_m = re.search(r"m_AmbientMode:\s*(\d+)", raw)
            fog_on = re.search(r"^\s*m_Fog:\s*(\d+)", raw, re.M)
            fogcol_m = re.search(
                r"m_FogColor:\s*\{r:\s*([\d.eE+-]+),\s*g:\s*([\d.eE+-]+),\s*b:\s*([\d.eE+-]+)",
                raw
            )
            fogden_m = re.search(r"m_FogDensity:\s*([\d.eE+-]+)", raw)
            has_env = False
            env_lines = []
            if ambient_m:
                ar, ag, ab_ = float(ambient_m.group(1)), float(ambient_m.group(2)), float(ambient_m.group(3))
                eqr, eqg, eqb = (float(equator_m.group(i)) for i in (1, 2, 3)) if equator_m else (ar, ag, ab_)
                gnr, gng, gnb = (float(ground_m.group(i)) for i in (1, 2, 3)) if ground_m else (ar, ag, ab_)
                if ar + ag + ab_ > 0.01:
                    load_steps += 2  # Environment + ProceduralSkyMaterial
                    sub_resources.append(
                        '[sub_resource type="ProceduralSkyMaterial" id="SkyMat"]\n'
                        f"sky_top_color = Color({ar:.4g}, {ag:.4g}, {ab_:.4g}, 1)\n"
                        f"sky_horizon_color = Color({eqr:.4g}, {eqg:.4g}, {eqb:.4g}, 1)\n"
                        f"ground_horizon_color = Color({gnr:.4g}, {gng:.4g}, {gnb:.4g}, 1)\n"
                        f"ground_bottom_color = Color({gnr:.4g}, {gng:.4g}, {gnb:.4g}, 1)\n"
                    )
                    sub_resources.append(
                        '[sub_resource type="Sky" id="Sky"]\n'
                        'sky_material = SubResource("SkyMat")\n'
                    )
                    env_lines = [
                        'environment = SubResource("Env")',
                    ]
                    has_env = True

            light_nodes = []
            for lm in re.finditer(r"--- !u!108 &(\d+)\s*\nLight:\s*\n([\s\S]*?)(?=--- !u!)", raw):
                lb = lm.group(2)
                ltype_m = re.search(r"m_Type:\s*(\d+)", lb)
                color_m = re.search(r"m_Color:\s*\{r:\s*([\d.eE+-]+),\s*g:\s*([\d.eE+-]+),\s*b:\s*([\d.eE+-]+)", lb)
                range_m = re.search(r"m_Range:\s*([\d.eE+-]+)", lb)
                inten_m = re.search(r"m_Intensity:\s*([\d.eE+-]+)", lb)
                go_m = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)", lb)

                ltype = ltype_m.group(1) if ltype_m else "2"
                lcol = (
                    float(color_m.group(1)),
                    float(color_m.group(2)),
                    float(color_m.group(3))
                ) if color_m else (1.0, 1.0, 1.0)
                lrange = float(range_m.group(1)) if range_m else 10.0
                linten = float(inten_m.group(1)) if inten_m else 1.0
                enabled_m = re.search(r"m_Enabled:\s*(\d+)", lb)
                if enabled_m and enabled_m.group(1) == "0":
                    continue
                shadow_m = re.search(r"m_Shadows:\s*\n\s*m_Type:\s*(\d+)", lb)
                shadow_enabled = bool(shadow_m and shadow_m.group(1) != "0")
                spot_angle_m = re.search(r"m_SpotAngle:\s*([\d.eE+-]+)", lb)
                spot_angle = float(spot_angle_m.group(1)) if spot_angle_m else 45.0

                # Find the Transform for this light's GameObject
                l_tf_id = None
                for tf_id, t in transforms.items():
                    if t["goid"] == (go_m.group(1) if go_m else None):
                        l_tf_id = tf_id
                        break

                if l_tf_id and l_tf_id in transforms:
                    lw_pos, lw_rot, _ = get_world_transform(l_tf_id)
                else:
                    lw_pos, lw_rot = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)

                godot_pos, godot_rot = unity_to_godot_transform(lw_pos, lw_rot)
                gx, gy, gz = godot_pos
                qx, qy, qz, qw = godot_rot
                basis = quat_to_basis(qx, qy, qz, qw)
                t_str = f"Transform3D({basis[0]:.6g}, {basis[1]:.6g}, {basis[2]:.6g}, {basis[3]:.6g}, {basis[4]:.6g}, {basis[5]:.6g}, {basis[6]:.6g}, {basis[7]:.6g}, {basis[8]:.6g}, {gx:.6g}, {gy:.6g}, {gz:.6g})"

                col_str = f"Color({lcol[0]:.4g}, {lcol[1]:.4g}, {lcol[2]:.4g}, 1)"
                energy = max(0.05, linten * 0.4)

                if ltype == "2":  # Point -> OmniLight3D
                    props = [
                        f"light_color = {col_str}",
                        f"light_energy = {energy:.4g}",
                        f"omni_range = {max(0.1, lrange):.6g}",
                        f"shadow_enabled = {'true' if shadow_enabled else 'false'}",
                    ]
                    light_nodes.append((f"PointLight_{lm.group(1)}", "OmniLight3D", t_str, props))
                elif ltype == "0":  # Spot -> SpotLight3D
                    props = [
                        f"light_color = {col_str}",
                        f"light_energy = {energy:.4g}",
                        f"spot_range = {max(0.1, lrange):.6g}",
                        f"spot_angle = {spot_angle:.6g}",
                        f"shadow_enabled = {'true' if shadow_enabled else 'false'}",
                    ]
                    light_nodes.append((f"SpotLight_{lm.group(1)}", "SpotLight3D", t_str, props))
                elif ltype == "1":  # Directional -> DirectionalLight3D
                    props = [
                        f"light_color = {col_str}",
                        f"light_energy = {max(0.05, linten * 0.25):.4g}",
                        f"shadow_enabled = {'true' if shadow_enabled else 'false'}",
                    ]
                    light_nodes.append((f"DirectionalLight_{lm.group(1)}", "DirectionalLight3D", t_str, props))

            camera_nodes = []
            cam_current_set = False
            for cm in re.finditer(r"--- !u!20 &(\d+)\s*\nCamera:\s*\n([\s\S]*?)(?=--- !u!)", raw):
                cb = cm.group(2)
                cgo_m = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)", cb)
                if not cgo_m:
                    continue
                c_tf_id = None
                for tf_id, t in transforms.items():
                    if t["goid"] == cgo_m.group(1):
                        c_tf_id = tf_id
                        break
                if not c_tf_id or c_tf_id not in transforms:
                    continue
                cw_pos, cw_rot, _ = get_world_transform(c_tf_id)
                godot_pos, godot_rot = unity_to_godot_transform(cw_pos, cw_rot)
                cx, cy, cz = godot_pos
                cqx, cqy, cqz, cqw = godot_rot
                c_basis = quat_to_basis(cqx, cqy, cqz, cqw)
                ct_str = f"Transform3D({c_basis[0]:.6g}, {c_basis[1]:.6g}, {c_basis[2]:.6g}, {c_basis[3]:.6g}, {c_basis[4]:.6g}, {c_basis[5]:.6g}, {c_basis[6]:.6g}, {c_basis[7]:.6g}, {c_basis[8]:.6g}, {cx:.6g}, {cy:.6g}, {cz:.6g})"
                fov_m = re.search(r"field of view:\s*([\d.eE+-]+)", cb)
                near_m = re.search(r"near clip plane:\s*([\d.eE+-]+)", cb)
                far_m = re.search(r"far clip plane:\s*([\d.eE+-]+)", cb)
                ortho_m = re.search(r"m_orthographic:\s*(\d+)", cb)
                size_m = re.search(r"orthographic size:\s*([\d.eE+-]+)", cb)
                enab_m = re.search(r"m_Enabled:\s*(\d+)", cb)
                gname_m = re.search(r"--- !u!1 &" + cgo_m.group(1) + r"\b\s*\nGameObject:\s*\n[\s\S]*?\nm_Name: ([^\n]+)", raw)
                cname = re.sub(r'[."/:]', "_", gname_m.group(1).strip()) if gname_m else ""
                if not cname or any(n[0] == cname for n in camera_nodes):
                    cname = f"Camera_{cm.group(1)}"
                cprops = []
                if ortho_m and ortho_m.group(1) == "1":
                    cprops.append("projection = 1")
                    if size_m:
                        cprops.append(f"size = {float(size_m.group(1)):.6g}")
                elif fov_m:
                    cprops.append(f"fov = {float(fov_m.group(1)):.6g}")
                if near_m:
                    cprops.append(f"near = {float(near_m.group(1)):.6g}")
                if far_m:
                    cprops.append(f"far = {float(far_m.group(1)):.6g}")
                if enab_m and enab_m.group(1) != "1":
                    cprops.append("enabled = false")
                elif not cam_current_set:
                    cprops.append("current = true")
                    cam_current_set = True
                camera_nodes.append((cname, "Camera3D", ct_str, cprops))

            if not has_env and not light_nodes and not nodes_data and not camera_nodes:
                continue

            if has_env:
                # Unity 0=Skybox and 1=Trilight both use environment sky data;
                # Unity 2=Flat and 3=Custom are closest to Godot color ambient light.
                ambient_mode = int(ambient_mode_m.group(1)) if ambient_mode_m else 0
                ambient_source = 3 if ambient_mode in (0, 1) else 2
                ambient_energy = float(ambient_intensity_m.group(1)) if ambient_intensity_m else 1.0
                env_body = (
                    '[sub_resource type="Environment" id="Env"]\n'
                    "background_mode = 2\n"
                    f"ambient_light_source = {ambient_source}\n"
                    f"ambient_light_color = Color({ar:.4g}, {ag:.4g}, {ab_:.4g}, 1)\n"
                    f"ambient_light_energy = {max(0.0, ambient_energy):.6g}\n"
                    "tonemap_mode = 2\n"
                )
                if fog_on and fog_on.group(1) == "1" and fogcol_m:
                    fr, fg_, fb = float(fogcol_m.group(1)), float(fogcol_m.group(2)), float(fogcol_m.group(3))
                    dens = float(fogden_m.group(1)) if fogden_m else 0.001
                    env_body += (
                        "fog_enabled = true\n"
                        f"fog_light_color = Color({fr:.4g}, {fg_:.4g}, {fb:.4g}, 1)\n"
                        f"fog_density = {max(0.0005, dens * dens * 150.0):.4g}\n"
                    )
                sub_resources.append(env_body)

            total_steps = len(ext_resources) + (len(sub_resources) if has_env else 0) + 1
            tscn_lines = [f'[gd_scene load_steps={total_steps} format=3]\n']
            for path, rid in ext_resources.items():
                tscn_lines.append(f'[ext_resource type="PackedScene" path="{path}" id="{rid}"]')

            for sr in sub_resources:
                tscn_lines.append("\n" + sr)

            tscn_lines.append(f'\n[node name="{scene_name}" type="Node3D"]')

            if has_env:
                tscn_lines.append(f'\n[node name="Environment" type="WorldEnvironment" parent="."]')
                for el in env_lines:
                    tscn_lines.append(el)

            for n_name, rid, t_str, nextra in nodes_data:
                tscn_lines.append(f'\n[node name="{n_name}" parent="." instance=ExtResource("{rid}")]')
                tscn_lines.append(f'transform = {t_str}')
                for x_line in nextra:
                    tscn_lines.append(x_line)

            for n_name, ntype, t_str, nprops in light_nodes:
                tscn_lines.append(f'\n[node name="{n_name}" type="{ntype}" parent="."]')
                tscn_lines.append(f'transform = {t_str}')
                for p_line in nprops:
                    tscn_lines.append(p_line)

            for n_name, ntype, t_str, nprops in camera_nodes:
                tscn_lines.append(f'\n[node name="{n_name}" type="{ntype}" parent="."]')
                tscn_lines.append(f'transform = {t_str}')
                for p_line in nprops:
                    tscn_lines.append(p_line)

            with open(out_tscn, "w", encoding="utf-8") as out:
                out.write("\n".join(tscn_lines) + "\n")

            compiled += 1
            if out_tscn not in categorized_files["tscn"]:
                categorized_files["tscn"].append(out_tscn)

        except Exception as exc:
            fail_warn(f"Failed to compile scene {uscene}: {exc}")

    return compiled


# ==============================================================================
# Pipeline Execution & CLI
# ==============================================================================
def run_pipeline(
    project_root: str,
    package_path: Optional[str] = None,
    extract_all: bool = False,
    purge_cache: bool = False
) -> None:
    print("==================================================")
    print("       Godot 4 Universal Synty Automator          ")
    print("==================================================")
    print(f"Target Project: {project_root}", flush=True)

    if not os.path.exists(os.path.join(project_root, "project.godot")):
        print(f"ERROR: No project.godot found at '{project_root}'!", flush=True)
        sys.exit(1)

    categorized_files = collect_project_files(project_root)

    # Step 0: Package Extraction
    packages_to_extract = []
    if package_path:
        packages_to_extract.append(package_path)
    elif extract_all:
        packages_to_extract.extend(categorized_files.get("unitypackages", []))

    if packages_to_extract:
        print(f"\n[0/6] Extracting {len(packages_to_extract)} UnityPackage(s)...", flush=True)
        for pkg in packages_to_extract:
            print(f"      - Unpacking {os.path.basename(pkg)}...", flush=True)
            extracted = extract_unitypackage(pkg, project_root)
            print(f"        -> Extracted {extracted} files.", flush=True)
        categorized_files = collect_project_files(project_root)

    print("\n[1/6] Sanitizing Textures & Resolving Missing Resources...", flush=True)
    fixed_tex, aliases, norm_tex = sanitize_and_resolve_textures(project_root, categorized_files)
    print(f"      - Fixed misnamed image formats: {fixed_tex}", flush=True)
    print(f"      - Generated missing texture stubs: {aliases}", flush=True)
    print(f"      - Normalized sRGB & normal map import settings: {norm_tex}", flush=True)

    print("\n[2/6] Parsing Unity Package Materials & Rebuilding StandardMaterial3D...", flush=True)
    rebuilt_mats, pack_prefab_mappings = synchronize_unitypackage_materials(project_root, categorized_files)
    total_bindings = sum(len(m) for m in pack_prefab_mappings.values())
    print(f"      - Synchronized & generated {rebuilt_mats} materials from Unity definitions.", flush=True)
    print(f"      - Loaded {total_bindings} deterministic model-to-material prefab bindings across {len(pack_prefab_mappings)} packs.", flush=True)

    print("\n[3/6] Deep Scanning & Mapping FBX Material Slots...", flush=True)
    models, slots = map_all_fbx_materials(project_root, categorized_files, pack_prefab_mappings)
    print(f"      - Mapped {slots} material slots across {models} FBX models.", flush=True)

    print("\n[4/6] Rectifying Character Rigs & Multi-Mesh Visibility...", flush=True)
    fixed_skels, fixed_chars = fix_character_rigs_and_visibility(categorized_files)
    print(f"      - Updated GeneralSkeleton -> Skeleton3D in {fixed_skels} scenes/prefabs.", flush=True)
    print(f"      - Applied selective mesh visibility to {fixed_chars} character prefabs.", flush=True)

    print("\n[5/6] Synchronizing Scene & Resource UIDs...", flush=True)
    synced_scenes, fixed_uids = synchronize_uids(project_root, categorized_files)
    print(f"      - Synchronized {fixed_uids} resource UIDs across {synced_scenes} scene files.", flush=True)

    print("\n[6/6] Compiling Unity Demo & Overview Scenes to Godot (.tscn)...", flush=True)
    compiled_scenes = compile_unity_scenes(project_root, categorized_files)
    print(f"      - Compiled {compiled_scenes} native Godot scene(s) from Unity definitions.", flush=True)

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

    if _ERRORS:
        print(f"\n>> Finished with {_ERRORS} warning(s); review the [WARN] lines above.", flush=True)

    print("\n==================================================", flush=True)
    print("Universal Synty automation completed successfully!", flush=True)
    print("Reload the project in Godot or restart the editor.", flush=True)
    print("==================================================", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Universal Synty asset importer and configuration engine for Godot 4.")
    parser.add_argument("--path", type=str, default=os.getcwd(), help="Path to Godot project root.")
    parser.add_argument("--package", "-pkg", type=str, default=None, help="Path to specific .unitypackage file to extract.")
    parser.add_argument("--extract-all", action="store_true", help="Extract all .unitypackage archives found in the project.")
    parser.add_argument("--purge-cache", action="store_true", help="Delete cached .scn files in .godot/imported.")
    args = parser.parse_args()
    run_pipeline(args.path, package_path=args.package, extract_all=args.extract_all, purge_cache=args.purge_cache)


if __name__ == "__main__":
    main()
