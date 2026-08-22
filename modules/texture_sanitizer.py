"""
Texture Sanitizer Module
------------------------
1. Inspects image files for format/extension mismatches using fast magic byte checks
   and converts them to valid images using Pillow only when a mismatch is detected.
2. Creates stub/alias texture files for hardcoded Maya/3ds Max workstation paths
   embedded in FBX binaries to prevent 404 image load errors during FBX import.
"""

import os
import re
from PIL import Image

EMBEDDED_ALIASES = {
    "targetUV_texture.psd": (512, 512, (200, 200, 200, 255)),
    "PolygonCyberCity_Texture_01_A.psd": "source_atlas",
    "PolygonCyberCity_Texture_01_B.psd": "source_atlas",
    "PolygonGeneric_Texture_01_A.psd": "source_atlas",
    "PolygonHorror_Texture_01.psd": "source_atlas",
    "PolygonShops_Walls_Texture_01.psd": "source_atlas",
    "PolygonShops_Texture_01.psd": "source_atlas",
    "PolygonFantasyGothic_Texture_01.psd": "source_atlas",
    "Wire_Alpha 1.tga": (512, 512, (255, 255, 255, 255)),
    "Glass.psd": (512, 512, (255, 255, 255, 100)),
    "Walls.psd": "source_atlas",
    "ParalaxTest.psd": "source_atlas",
}

IGNORED_DIRS = {".godot", ".git", "node_modules"}


def save_image_safe(img: Image.Image, target_path: str) -> None:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    ext = os.path.splitext(target_path)[1].lower()
    fmt = "PNG" if ext in [".png", ".psd"] else ("TGA" if ext == ".tga" else "JPEG")
    img.save(target_path, format=fmt)


def sanitize_texture_formats(project_root: str) -> int:
    fixed_count = 0
    image_extensions = {".png", ".tga", ".jpg", ".jpeg"}

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for file_name in files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in image_extensions:
                file_path = os.path.join(root, file_name)
                try:
                    with open(file_path, "rb") as f:
                        header = f.read(16)

                    mismatch = False
                    is_png_bytes = header.startswith(b"\x89PNG\r\n\x1a\n")
                    is_jpg_bytes = header.startswith(b"\xff\xd8\xff")

                    if ext == ".png" and not is_png_bytes:
                        mismatch = True
                    elif ext == ".tga" and (is_png_bytes or is_jpg_bytes):
                        mismatch = True
                    elif ext in [".jpg", ".jpeg"] and not is_jpg_bytes:
                        mismatch = True

                    if mismatch:
                        with Image.open(file_path) as img:
                            save_format = "PNG" if ext == ".png" else ("TGA" if ext == ".tga" else "JPEG")
                            img.save(file_path, format=save_format)
                            fixed_count += 1
                except Exception:
                    pass

    return fixed_count


def create_embedded_texture_aliases(project_root: str) -> int:
    created_count = 0

    cyber_atlas_path = None
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        if "PolygonCyberCity_Texture_01_A.png" in files:
            cyber_atlas_path = os.path.join(root, "PolygonCyberCity_Texture_01_A.png")
            break

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        if os.path.basename(root) in ["Models", "Base"] and "Synty" in root:
            for alias_name, spec in EMBEDDED_ALIASES.items():
                target_path = os.path.join(root, alias_name)
                if not os.path.exists(target_path):
                    try:
                        if spec == "source_atlas" and cyber_atlas_path and os.path.exists(cyber_atlas_path):
                            with Image.open(cyber_atlas_path) as img:
                                save_image_safe(img, target_path)
                            created_count += 1
                        elif isinstance(spec, tuple):
                            w, h, color = spec
                            img = Image.new("RGBA", (w, h), color)
                            save_image_safe(img, target_path)
                            created_count += 1
                    except Exception:
                        pass

    apocalypse_tex_dir = os.path.join(project_root, "Assets/PolygonApocalypse/Textures/Misc")
    os.makedirs(apocalypse_tex_dir, exist_ok=True)

    source_apoc = os.path.join(project_root, "Assets/Synty/AnimationBaseLocomotion/Samples/Textures/T_PolygonApocalypse_01.png")
    if not os.path.exists(source_apoc):
        source_apoc = os.path.join(project_root, "Assets/AnimationBaseLocomotion/Samples/Textures/T_PolygonApocalypse_01.png")

    apoc_main = os.path.join(project_root, "Assets/PolygonApocalypse/Textures/PolygonApocalypse_Texture_01_A 1.png")
    if not os.path.exists(apoc_main) and os.path.exists(source_apoc):
        os.makedirs(os.path.dirname(apoc_main), exist_ok=True)
        with Image.open(source_apoc) as img:
            save_image_safe(img, apoc_main)
        created_count += 1

    apoc_emissive = os.path.join(apocalypse_tex_dir, "PolygonApocalypse_Emissive_01.png")
    if not os.path.exists(apoc_emissive):
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
        save_image_safe(img, apoc_emissive)
        created_count += 1

    apoc_normal = os.path.join(apocalypse_tex_dir, "PolygonApocalypse_Normal.png")
    if not os.path.exists(apoc_normal):
        img = Image.new("RGBA", (256, 256), (128, 128, 255, 255))
        save_image_safe(img, apoc_normal)
        created_count += 1

    sky_target = os.path.join(project_root, "Assets/AnimationBaseLocomotion/Samples/Meshes/SimpleSky.png")
    if not os.path.exists(sky_target):
        os.makedirs(os.path.dirname(sky_target), exist_ok=True)
        img = Image.new("RGBA", (256, 256), (135, 206, 235, 255))
        save_image_safe(img, sky_target)
        created_count += 1

    # Sidekick published texture stubs
    sidekick_color_map = os.path.join(project_root, "Assets/Synty/SidekickCharacters/Resources/Textures/T_ColorMap.png")
    sidekick_target_1 = os.path.join(project_root, "Assets/Synty/_SidekickCharacters/_published/_textures/Base_ColorLabels_01.png")
    sidekick_target_2 = os.path.join(project_root, "Assets/Synty/_SidekickCharacters/_Textures/_Working/Base_Color_01.png")

    for sk_target in [sidekick_target_1, sidekick_target_2]:
        if not os.path.exists(sk_target):
            os.makedirs(os.path.dirname(sk_target), exist_ok=True)
            if os.path.exists(sidekick_color_map):
                with Image.open(sidekick_color_map) as img:
                    save_image_safe(img, sk_target)
            else:
                img = Image.new("RGBA", (512, 512), (200, 200, 200, 255))
                save_image_safe(img, sk_target)
            created_count += 1

    # Dropbox workstation texture stubs
    generic_atlas = os.path.join(project_root, "Assets/Synty/PolygonGeneric/Textures/PolygonGeneric_Texture_01_A.png")
    dropbox_paths = [
        os.path.join(project_root, "Dropbox/SyntyStudios/Polygon_Generic_Assets/_Working/_Textures/PolygonGeneric_Texture_01_A.psd"),
        os.path.join(project_root, "Assets/Synty/Dropbox/SyntyStudios/Polygon_Generic_Assets/_Working/_Textures/PolygonGeneric_Texture_01_A.psd"),
        os.path.join(project_root, "Dropbox/SyntyStudios/PolygonCyberCity/_Working/_Textures/PolygonCyberCity_Texture_01_A.psd"),
        os.path.join(project_root, "Assets/Synty/Dropbox/SyntyStudios/PolygonCyberCity/_Working/_Textures/PolygonCyberCity_Texture_01_A.psd"),
    ]
    for db_target in dropbox_paths:
        if not os.path.exists(db_target):
            os.makedirs(os.path.dirname(db_target), exist_ok=True)
            if os.path.exists(generic_atlas):
                with Image.open(generic_atlas) as img:
                    save_image_safe(img, db_target)
            elif cyber_atlas_path and os.path.exists(cyber_atlas_path):
                with Image.open(cyber_atlas_path) as img:
                    save_image_safe(img, db_target)
            else:
                img = Image.new("RGBA", (512, 512), (200, 200, 200, 255))
                save_image_safe(img, db_target)
            created_count += 1

    return created_count


def normalize_texture_imports(project_root: str) -> int:
    updated_count = 0
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for file_name in files:
            if file_name.endswith((".png.import", ".tga.import", ".jpg.import", ".webp.import")):
                if "normal" in file_name.lower():
                    continue
                import_path = os.path.join(root, file_name)
                try:
                    with open(import_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    modified = False
                    if "compress/normal_map=2" in content or "compress/normal_map=1" in content:
                        content = re.sub(r"compress/normal_map=\d+", "compress/normal_map=0", content)
                        modified = True
                    if "roughness/mode=1" in content or "roughness/mode=2" in content:
                        content = re.sub(r"roughness/mode=\d+", "roughness/mode=0", content)
                        modified = True

                    if modified:
                        with open(import_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        updated_count += 1
                except Exception:
                    pass
    return updated_count
