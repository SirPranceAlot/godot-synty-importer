# Godot Synty Asset Importer & Automator

A comprehensive, automated tool and Godot 4 editor addon to seamlessly import, repair, map, and optimize **Synty Studios** 3D asset packs in Godot 4.

---

## 🎯 Problems Solved

When importing Synty asset packs into Godot 4, several recurring engine incompatibilities arise:

1. **Untextured / White Models**: Synty FBX models use internal Maya/3ds Max material slots (`MAT_01A`, `Scifi_1a9`, `SciFi_Planets_SHD*`, `CyberCityTexture*`, `Base_Lambert*`) that Godot imports as plain white without texture assignments.
2. **Missing Embedded Workstation Paths**: FBX binaries often contain obsolete local paths from the 3D artists' machines (e.g. `Dropbox/.../targetUV_texture.psd`), triggering 404 image errors on import.
3. **Multi-Character Rig Collisions**: Packs with multi-character files (e.g. `Characters.fbx` containing 18+ characters under a single skeleton) render all 18 characters overlapping simultaneously unless inactive sub-meshes are hidden.
4. **Invalid UID Warnings**: Unity-to-Godot converters often generate scene files with mismatched resource UIDs, filling the output console with `invalid UID: ... using text path instead` warnings.
5. **Normal Map Washouts**: Low-poly Synty meshes lacking vertex tangents (`ARRAY_FORMAT_TANGENT`) render completely white when normal maps are enabled.

---

## ✨ Features

- **Automated FBX Binary Header Parsing**: Detects internal Maya/3ds Max material slots in all `.fbx` files and generates clean native Godot `.fbx.import` mappings.
- **Selective Character Visibility**: Automatically updates character prefabs to show only their designated character and hides all 17+ non-active variations.
- **Texture Format Sanitization**: Detects misnamed image formats (e.g. TGA data named `.png`) and converts them using Pillow.
- **Embedded Alias Stubs**: Automatically provisions transparent/neutral texture aliases for legacy FBX embedded paths.
- **Project-Wide UID Synchronization**: Re-indexes and updates all scene and prefab UIDs to guarantee zero console warnings.
- **One-Click Godot 4 Addon**: Run directly inside the Godot Editor via **Project > Tools > Fix Synty Asset Packs**.

---

## 🚀 Installation & Usage

### Method 1: Python CLI Tool (Standalone)

1. Clone or copy this repository:
   ```bash
   git clone https://github.com/your-username/godot-synty-importer.git
   cd godot-synty-importer
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run against your Godot 4 project:
   ```bash
   python3 synty_automator.py --path "/path/to/your/godot_project"
   ```

### Method 2: Godot 4 Editor Plugin

1. Copy the `addons/synty_importer` folder into your Godot project's `addons/` directory:
   ```text
   res://addons/synty_importer/
   ```
2. Open Godot and go to **Project > Project Settings > Plugins**.
3. Enable **Synty Importer & Automator**.
4. Click **Project > Tools > Fix Synty Asset Packs** to run the complete pipeline at any time.

---

## 🛠 Project Structure

```text
godot-synty-importer/
├── synty_automator.py               # Main CLI automation pipeline
├── modules/
│   ├── texture_sanitizer.py         # Image format verification & stub generator
│   ├── fbx_slot_mapper.py           # FBX binary slot parser & .import writer
│   ├── character_prefab_fixer.py    # Skeleton3D migration & visibility setup
│   └── uid_synchronizer.py          # Scene & resource UID synchronizer
└── addons/synty_importer/           # Godot 4 Editor Plugin
    ├── plugin.cfg
    ├── plugin.gd
    └── synty_post_import.gd         # Native EditorScenePostImport script
```

---

## 📄 License

MIT License. Free for use in personal and commercial Godot projects.
