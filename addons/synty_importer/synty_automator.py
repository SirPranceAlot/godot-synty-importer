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
  - Headless cache purging for seamless Godot editor reloads

Usage:
    python3 synty_automator.py [--path /path/to/godot_project] [--extract-all] [--package /path/to/pack.unitypackage] [--purge-cache]
"""

import argparse
import mmap
import os
import re
import struct
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
RE_RES_TEX = re.compile(r'path="res://([^"]+\.(?:png|psd|tga|jpg|jpeg|webp))"', re.IGNORECASE)
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


def is_default_atlas_key(k: str) -> bool:
    low = k.lower()
    if any(sub in low for sub in ["hack", "branch", "glass", "damage", "alt", "skin", "poster", "wall", "floor", "brick", "junk", "trash", "gradient", "holo"]):
        return False
    return low.endswith("01_a") or "colormap" in low or "palette" in low or "base_color" in low or "texture_01" in low or "01_a" in low


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
    extracted = 0
    for guid, rel in guid_to_path.items():
        if not rel or guid not in guid_to_asset:
            continue
        target = os.path.join(destination_root, rel)
        if not os.path.realpath(target).startswith(os.path.realpath(destination_root) + os.sep):
            fail_warn(f"Skipped unsafe path in package: {rel}")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            with open(target, "wb") as out:
                out.write(guid_to_asset[guid])
            extracted += 1
        except Exception as exc:
            fail_warn(f"Failed to extract {rel}: {exc}")
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

    is_transparent = "RenderType: Transparent" in raw_yaml or "_SURFACE_TYPE_TRANSPARENT" in raw_yaml
    is_cutout = "RenderType: TransparentCutout" in raw_yaml or "_ALPHATEST_ON" in raw_yaml or floats.get("_AlphaClip", 0) == 1
    is_holo = "Hologram" in raw_yaml or "_Neon_Color" in colors or "_Holo_Lines" in texs

    return {
        "texs": texs,
        "colors": colors,
        "floats": floats,
        "is_transparent": is_transparent,
        "is_cutout": is_cutout,
        "is_holo": is_holo,
    }


def resolve_texture_res_path(tex_rel_path: Optional[str], project_root: str, pack_dir: str) -> Optional[str]:
    if not tex_rel_path:
        return None
    clean_rel = tex_rel_path.replace("\\", "/").lstrip("/")
    full_path = os.path.join(project_root, clean_rel)
    if os.path.exists(full_path):
        return "res://" + clean_rel
    fname = os.path.basename(clean_rel)
    if fname:
        for root, _, files in os.walk(pack_dir):
            for f in files:
                if f.lower() == fname.lower():
                    found_rel = os.path.relpath(os.path.join(root, f), project_root).replace("\\", "/")
                    return "res://" + found_rel
    return "res://" + clean_rel


def generate_godot_material_from_unity(
    mat_stem: str,
    parsed: Dict[str, Any],
    project_root: str,
    pack_dir: str
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

    # 1. Albedo Texture & Color
    albedo_res_id = None
    albedo_tex_path = texs.get("_Albedo_Map") or texs.get("_MainTex") or texs.get("_BaseMap")
    if not albedo_tex_path and is_holo:
        albedo_tex_path = texs.get("_Holo_Lines")

    if albedo_tex_path:
        rel_albedo = resolve_texture_res_path(albedo_tex_path, project_root, pack_dir)
        if rel_albedo:
            albedo_res_id = f"{res_idx}_tex"
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_albedo}" id="{albedo_res_id}"]')
            properties.append(f'albedo_texture = ExtResource("{albedo_res_id}")')
            res_idx += 1

    base_col = colors.get("_Base_Color") or colors.get("_BaseColor") or colors.get("_Color")
    if base_col and not is_holo:
        properties.append(f'albedo_color = Color({base_col[0]:.6g}, {base_col[1]:.6g}, {base_col[2]:.6g}, {base_col[3]:.6g})')

    # 2. Transparency, Cull, and Shading
    if is_cutout:
        properties.append("transparency = 2")
        properties.append(f'alpha_scissor_threshold = {floats.get("_Cutoff", 0.5):.6g}')
    elif is_transparent or is_holo:
        properties.append("transparency = 1")

    cull_val = floats.get("_Cull", 2)
    if is_holo or cull_val == 0:
        properties.append("cull_mode = 2")
    elif cull_val == 1:
        properties.append("cull_mode = 1")
    else:
        properties.append("cull_mode = 0")

    if is_holo:
        properties.append("shading_mode = 0")

    # 3. Normal Map
    norm_path = texs.get("_Normal_Map") or texs.get("_BumpMap")
    if norm_path:
        rel_norm = resolve_texture_res_path(norm_path, project_root, pack_dir)
        if rel_norm:
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_norm}" id="{res_idx}_normal"]')
            properties.append("normal_enabled = true")
            properties.append(f'normal_texture = ExtResource("{res_idx}_normal")')
            res_idx += 1

    # 4. Occlusion (AO)
    ao_path = texs.get("_OcclusionMap") or texs.get("_AO_Map")
    if ao_path:
        rel_ao = resolve_texture_res_path(ao_path, project_root, pack_dir)
        if rel_ao:
            ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_ao}" id="{res_idx}_ao"]')
            properties.append("ao_enabled = true")
            properties.append(f'ao_texture = ExtResource("{res_idx}_ao")')
            res_idx += 1

    # 5. Emission
    emissive_tex_path = texs.get("_Emission_Mask") or texs.get("_Emission_Map") or texs.get("_EmissionMap")
    neon_col = colors.get("_Neon_Color") or colors.get("_Neon_Colour_01")
    em_col = colors.get("_EmissionColor") or colors.get("_Emission_Color")

    has_emission = bool(emissive_tex_path or (neon_col and max(neon_col[:3]) > 0.01) or (em_col and max(em_col[:3]) > 0.01))
    if has_emission or is_holo:
        properties.append("emission_enabled = true")
        final_em_col = neon_col or em_col or (1.0, 1.0, 1.0, 1.0)
        properties.append(f'emission = Color({final_em_col[0]:.6g}, {final_em_col[1]:.6g}, {final_em_col[2]:.6g}, 1)')
        em_power = floats.get("_Emission_Power", floats.get("_EmissionStrength", 2.0))
        if is_holo and em_power < 1.0:
            em_power = 2.0
        properties.append(f'emission_energy_multiplier = {em_power:.6g}')
        if emissive_tex_path:
            rel_em = resolve_texture_res_path(emissive_tex_path, project_root, pack_dir)
            if rel_em:
                ext_resources.append(f'[ext_resource type="Texture2D" path="{rel_em}" id="{res_idx}_emission"]')
                properties.append(f'emission_texture = ExtResource("{res_idx}_emission")')
                res_idx += 1
        elif albedo_res_id and is_holo:
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
            elif low.endswith(".unitypackage"):
                categories["unitypackages"].append(full_path)
    return categories


def save_image_safe(img: Optional[Any], target_path: str) -> None:
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
        is_atlas = any(k in low for k in ["colormap", "texture_01", "base_color", "diffuse", "palette", "atlas", "main"]) and "dst" not in low and not any(sub in low for sub in ["hack", "branch", "glass"])
        if is_atlas:
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
                referenced_textures.add(rel)
        except Exception:
            pass

    scan_targets = categorized_files.get("materials", []) + categorized_files.get("tscn", [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(scan_tex_refs, scan_targets))

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

    # 3. FBX Embedded Texture Dependencies
    for fbx_p in categorized_files.get("fbx", []):
        fbx_d = os.path.dirname(fbx_p)
        g = parse_fbx_graph(fbx_p)
        needed_files = set()
        for vid in g.get("videos", {}).values():
            if vid:
                needed_files.add(os.path.basename(vid.replace("\\", "/")))
        for tex_name, fname in g.get("textures", {}).values():
            if fname:
                needed_files.add(os.path.basename(fname.replace("\\", "/")))

        for req in needed_files:
            if not req:
                continue
            fbx_target = os.path.join(fbx_d, req)
            if not os.path.exists(fbx_target):
                pack_dir = get_pack_root(fbx_target)
                req_stem = os.path.splitext(req)[0].lower()
                matched_tex = None
                for t_candidate in categorized_files.get("images", []):
                    if get_pack_root(t_candidate) == pack_dir and os.path.splitext(os.path.basename(t_candidate))[0].lower() == req_stem:
                        matched_tex = t_candidate
                        break
                src_atlas = matched_tex or pack_atlases.get(pack_dir) or global_default_atlas
                try:
                    if HAS_PIL and src_atlas and os.path.exists(src_atlas):
                        with Image.open(src_atlas) as img:
                            save_image_safe(img, fbx_target)
                    else:
                        save_image_safe(None, fbx_target)
                    aliases_created += 1
                except Exception:
                    pass

    # 4. Texture .import normalizer
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
            curr += 12 + comp_len
            props.append(None)
        else:
            break
    return props, curr


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
    textures: Dict[int, Tuple[str, str]] = {}
    videos: Dict[int, str] = {}
    if objects:
        for c in objects[2]:
            c_name, c_props, c_children = c
            if c_name == "Material" and len(c_props) >= 2:
                mat_id = c_props[0]
                mat_name = str(c_props[1]).split(chr(0))[0].split("::")[-1]
                materials[mat_id] = mat_name
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
    if connections_node:
        for c in connections_node[2]:
            c_props = c[1]
            if len(c_props) >= 3 and c_props[0] == "OO":
                child_id, parent_id = c_props[1], c_props[2]
                if parent_id in materials and child_id in textures:
                    tname, tfname = textures[child_id]
                    vfname = videos.get(child_id, "")
                    final_path = tfname or vfname or tname
                    mat_to_textures.setdefault(parent_id, []).append(final_path)

    slot_to_tex: Dict[str, List[str]] = {}
    for mid, mname in materials.items():
        slot_to_tex[mname] = mat_to_textures.get(mid, [])

    return {
        "materials": materials,
        "textures": textures,
        "videos": videos,
        "mat_to_textures": slot_to_tex
    }


# ==============================================================================
# 4. Deterministic Material Synchronization & Slot Resolution
# ==============================================================================
def synchronize_unitypackage_materials(
    project_root: str,
    categorized_files: Dict[str, List[str]]
) -> Tuple[int, Dict[str, Dict[str, List[str]]]]:
    updated_mats = 0
    pack_prefab_mats: Dict[str, Dict[str, List[str]]] = {}

    def add_prefab_mats(pack_key: str, model_key: str, mats: List[str]) -> None:
        norm_pack = os.path.normpath(pack_key)
        pack_dict = pack_prefab_mats.setdefault(norm_pack, {})
        existing = pack_dict.setdefault(model_key, [])
        for m in mats:
            if m not in existing:
                existing.append(m)

    # 1. Parse Unity Packages
    for pkg in categorized_files.get("unitypackages", []):
        guid_to_path, guid_to_asset = read_unitypackage_data(pkg)
        if not guid_to_path:
            continue

        mat_guid_to_res: Dict[str, str] = {}
        for guid, rel_path in guid_to_path.items():
            if rel_path.endswith(".mat") and guid in guid_to_asset:
                raw_yaml = guid_to_asset[guid].decode("utf-8", errors="ignore")
                parsed = parse_unity_mat_yaml(raw_yaml, guid_to_path)
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

                mat_content = generate_godot_material_from_unity(mat_stem, parsed, project_root, pack_dir)
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
                
                mat_paths = []
                for block in re.finditer(r"m_Materials:\s*\n((?:\s*-\s*\{fileID:[^\}]+\}\s*\n?)+)", raw_yaml):
                    for mg in re.findall(r"guid:\s*([a-f0-9]{32})", block.group(1)):
                        res_p = mat_guid_to_res.get(mg)
                        if not res_p:
                            mp = guid_to_path.get(mg)
                            if mp and mp.endswith(".mat"):
                                res_p = "res://" + mp.replace("\\", "/") + ".tres"
                        if res_p:
                            mat_paths.append(res_p)

                for block in re.finditer(r"propertyPath:\s*m_Materials\.Array\.data\[(\d+)\]\s*\n\s*value:\s*\n\s*objectReference:\s*\{fileID:[^,]+,\s*guid:\s*([a-f0-9]{32})", raw_yaml):
                    idx, mg = int(block.group(1)), block.group(2)
                    res_p = mat_guid_to_res.get(mg)
                    if not res_p:
                        mp = guid_to_path.get(mg)
                        if mp and mp.endswith(".mat"):
                            res_p = "res://" + mp.replace("\\", "/") + ".tres"
                    if res_p:
                        while len(mat_paths) <= idx:
                            mat_paths.append(res_p)
                        mat_paths[idx] = res_p

                if mat_paths:
                    mesh_guids = re.findall(r"m_Mesh:\s*\{fileID:[^,]+,\s*guid:\s*([a-f0-9]{32})", raw_yaml)
                    for msh in mesh_guids:
                        mesh_path = guid_to_path.get(msh, "")
                        if mesh_path:
                            model_stem = os.path.splitext(os.path.basename(mesh_path))[0].lower()
                            add_prefab_mats(pack_dir, model_stem, mat_paths)
                    prefab_stem = os.path.splitext(os.path.basename(rel_path))[0].lower()
                    add_prefab_mats(pack_dir, prefab_stem, mat_paths)

    # 2. Synchronize standalone on-disk .mat files
    for mpath in categorized_files.get("materials", []):
        if mpath.endswith(".mat"):
            target_mat = mpath + ".tres"
            pack_dir = get_pack_root(mpath)
            mat_stem = os.path.splitext(os.path.basename(mpath))[0]
            try:
                with open(mpath, "r", encoding="utf-8", errors="ignore") as fh:
                    raw_yaml = fh.read()
                parsed = parse_unity_mat_yaml(raw_yaml, {})
                mat_content = generate_godot_material_from_unity(mat_stem, parsed, project_root, pack_dir)
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
                    m_list = list(ext_mats.values())
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
    if prefab_mats and len(prefab_mats) > 0:
        if slot_index < len(prefab_mats):
            return prefab_mats[slot_index]
        return prefab_mats[0]

    skip_sfx = ("_normal", "_normals", "_n", "_emissive", "_emission", "emissive", "emission", "_occlusion", "_ao", "_mask", "_alpha")
    diffuse_candidates = [t for t in connected_tex if not any(sfx in os.path.basename(t).lower() for sfx in skip_sfx)]
    search_order = diffuse_candidates if diffuse_candidates else connected_tex

    for tex in search_order:
        low = os.path.basename(tex).lower()
        stem = normalize_tex_stem(low)
        if stem in tex_to_mat:
            return tex_to_mat[stem]

    s_low = slot.lower()
    if s_low in mats:
        return mats[s_low]

    norm_slot = RE_CLEAN_PREFIX.sub("", s_low)
    norm_slot = RE_CLEAN_SUFFIX.sub("", norm_slot)
    if norm_slot in mats:
        return mats[norm_slot]
    norm_stem = normalize_tex_stem(norm_slot)
    if norm_stem in tex_to_mat:
        return tex_to_mat[norm_stem]
    if norm_stem in mats:
        return mats[norm_stem]

    if any(term in s_low for term in ["wall", "brick", "building"]):
        num_m = RE_NUM_EXT.search(slot)
        if num_m:
            target_key = f"wall_{num_m.group(1).zfill(2)}"
            for k in [f"{target_key}_a", f"{target_key}_b", target_key]:
                if k in mats:
                    return mats[k]

    return default_atlas


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
        stem = os.path.basename(p).replace(".mat.tres", "").replace(".tres", "").lower()
        norm_mat_stem = normalize_tex_stem(stem)
        rel = "res://" + os.path.relpath(p, project_root).replace("\\", "/")
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
                if model_stem in p_dict:
                    pref_mats = p_dict[model_stem]
                    break

        default_atlas = ""
        for k, v in p_mats.items():
            if is_default_atlas_key(k):
                default_atlas = v
                break
        if not default_atlas:
            default_atlas = next(iter(p_mats.values()), next(iter(all_materials.values()), ""))

        content = ""
        if os.path.exists(imp_path):
            with open(imp_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
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

        sub_json_lines = ['_subresources={\n"materials": {']
        for i, (k, v) in enumerate(mat_dict.items()):
            comma = "," if i < len(mat_dict) - 1 else ""
            sub_json_lines.append(f'"{k}": {{\n"use_external/enabled": true,\n"use_external/path": "{v["use_external/path"]}"\n}}{comma}')
        sub_json_lines.append("}\n}")
        new_sub = "\n".join(sub_json_lines)

        if "_subresources={" in content:
            content = re.sub(r"_subresources=\{[\s\S]*?\n\}", new_sub, content)
        else:
            content += "\n" + new_sub + "\n"

        if "fbx/embedded_image_handling" not in content:
            params_idx = content.find("[params]")
            if params_idx != -1:
                content = content[:params_idx + 8] + "\nfbx/importer=0\nfbx/embedded_image_handling=0\nmaterials/extract=0" + content[params_idx + 8:]
            else:
                content += "\nfbx/importer=0\nfbx/embedded_image_handling=0\nmaterials/extract=0\n"

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
        print(f"\n[0/5] Extracting {len(packages_to_extract)} UnityPackage(s)...", flush=True)
        for pkg in packages_to_extract:
            print(f"      - Unpacking {os.path.basename(pkg)}...", flush=True)
            extracted = extract_unitypackage(pkg, project_root)
            print(f"        -> Extracted {extracted} files.", flush=True)
        # Refresh file index after extraction
        categorized_files = collect_project_files(project_root)

    print("\n[1/5] Sanitizing Textures & Resolving Missing Resources...", flush=True)
    fixed_tex, aliases, norm_tex = sanitize_and_resolve_textures(project_root, categorized_files)
    print(f"      - Fixed misnamed image formats: {fixed_tex}", flush=True)
    print(f"      - Generated missing texture & PSD alias stubs: {aliases}", flush=True)
    print(f"      - Normalized sRGB & normal map import settings: {norm_tex}", flush=True)

    print("\n[2/5] Parsing Unity Package Materials & Rebuilding StandardMaterial3D...", flush=True)
    rebuilt_mats, pack_prefab_mappings = synchronize_unitypackage_materials(project_root, categorized_files)
    total_bindings = sum(len(m) for m in pack_prefab_mappings.values())
    print(f"      - Synchronized & generated {rebuilt_mats} materials from Unity definitions.", flush=True)
    print(f"      - Loaded {total_bindings} deterministic model-to-material prefab bindings across {len(pack_prefab_mappings)} packs.", flush=True)

    print("\n[3/5] Deep Scanning & Mapping FBX Material Slots...", flush=True)
    models, slots = map_all_fbx_materials(project_root, categorized_files, pack_prefab_mappings)
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
