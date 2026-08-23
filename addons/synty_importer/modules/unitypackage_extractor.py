"""
UnityPackage Extractor Module
-----------------------------
Extracts assets from standard compressed .unitypackage (tar.gz) archives directly
into a Godot project directory by reading GUID directory metadata (pathname & asset).
"""

import os
import tarfile
from typing import List, Tuple, Set


def extract_unitypackage(package_path: str, destination_root: str) -> Tuple[int, Set[str]]:
    """
    Extracts all assets from a .unitypackage file into destination_root.
    Reconstructs the original folder hierarchy using the 'pathname' file inside each GUID folder.
    Returns (extracted_file_count, set_of_affected_pack_dirs).
    """
    if not os.path.exists(package_path):
        raise FileNotFoundError(f"Package not found: {package_path}")

    extracted_count = 0
    affected_packs = set()

    # Open the tar.gz archive
    with tarfile.open(package_path, "r:*") as tar:
        # 1. Map guid -> { 'pathname': str, 'asset_member': TarInfo, 'meta_member': TarInfo }
        guid_entries = {}
        for member in tar.getmembers():
            parts = member.name.replace("\\", "/").split("/")
            if len(parts) >= 2:
                guid = parts[0]
                item_name = parts[1]
                if guid not in guid_entries:
                    guid_entries[guid] = {}
                guid_entries[guid][item_name] = member

        # 2. Extract each valid asset
        for guid, items in guid_entries.items():
            if "pathname" in items and "asset" in items:
                try:
                    # Read destination pathname
                    pathname_file = tar.extractfile(items["pathname"])
                    if pathname_file is None:
                        continue
                    dest_rel_path = pathname_file.read().decode("utf-8", errors="ignore").splitlines()[0].strip()
                    if not dest_rel_path:
                        continue

                    # Construct target path
                    target_full_path = os.path.join(destination_root, dest_rel_path)
                    os.makedirs(os.path.dirname(target_full_path), exist_ok=True)

                    # Extract the asset data
                    asset_file = tar.extractfile(items["asset"])
                    if asset_file:
                        with open(target_full_path, "wb") as out_f:
                            out_f.write(asset_file.read())
                        extracted_count += 1

                        # Track affected pack root
                        parts = dest_rel_path.replace("\\", "/").split("/")
                        if len(parts) >= 3 and parts[0].lower() == "assets" and parts[1].lower() == "synty":
                            affected_packs.add(os.path.join(destination_root, parts[0], parts[1], parts[2]))
                        elif len(parts) >= 2 and parts[0].lower() == "assets":
                            affected_packs.add(os.path.join(destination_root, parts[0], parts[1]))
                except Exception:
                    pass

    return extracted_count, affected_packs
