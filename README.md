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

- **Universal Synty Pack Compatibility**: Fully dynamic heuristics support any Synty Studios pack (Polygon, Simple, Sidekick, Fantasy, Sci-Fi, Dungeon, City, Apocalypse, Western, Samurai, Nature, etc.).
- **Dynamic Missing Texture & PSD Resolver**: Dynamically scans all scenes, materials, and FBX binaries, auto-generating neutral and atlas texture stubs so missing embedded paths never crash or trigger 404 image errors.
- **Automated StandardMaterial3D Generation**: Automatically generates Godot 4 `StandardMaterial3D` resources for any asset pack that only includes raw FBX meshes and textures.
- **Deep FBX Binary Slot Parsing**: Detects internal Maya/3ds Max material slots in all `.fbx` files and applies a 7-tier semantic fallback system to map them directly into native Godot `.fbx.import` files.
- **Universal Multi-Character Rig Visibility**: Automatically normalizes skeleton hierarchies to `Skeleton3D` and sets selective mesh visibility on multi-character prefabs.
- **Texture Format Sanitization**: Detects misnamed image formats (e.g. TGA data named `.png`) and normalizes sRGB / normal map compression flags.
- **Direct .unitypackage Import**: Extract raw `.unitypackage` files directly into Godot and configure all assets automatically in one step.
- **Project-Wide UID Synchronization**: Re-indexes and updates all scene and prefab UIDs to guarantee zero console warnings.
- **One-Click Godot 4 Addon**: Run directly inside the Godot Editor via **Project > Tools > Fix Synty Asset Packs** or **Project > Tools > Import Synty .unitypackage...**.
- **Direct Unity Scene Compilation**: Compiles raw Unity `.unity` scene files into Godot `.tscn` scenes.

---

## 🚀 Installation & Usage

### Method 1: Python CLI Tool (Standalone)

1. Clone this repository:
   ```bash
   git clone https://github.com/SirPranceAlot/godot-synty-importer.git
   cd godot-synty-importer
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. **Import a `.unitypackage` directly:**
   ```bash
   python3 addons/synty_importer/synty_automator.py --path "/path/to/godot_project" --package "/path/to/PolygonCyberCity.unitypackage"
   ```
   *Or extract all packages found in the project:*
   ```bash
   python3 addons/synty_importer/synty_automator.py --path "/path/to/godot_project" --extract-all
   ```
   *Or run against an already-extracted asset pack:*
   ```bash
   python3 addons/synty_importer/synty_automator.py --path "/path/to/godot_project"
   ```
   *Optional: purge stale compiled `.scn` cache:*
   ```bash
   python3 addons/synty_importer/synty_automator.py --path "/path/to/godot_project" --purge-cache
   ```

### Method 2: Godot 4 Editor Plugin

#### Prerequisites
The editor plugin executes the background automator using Python. Ensure your system has:
- **Python 3.8+** in your system `PATH` (`python` or `python3`)
- **Pillow** installed: `pip install Pillow` (or `pip install -r requirements.txt`)

#### Setup
1. Copy the `addons/synty_importer` folder into your Godot project's `addons/` directory:
   ```text
   res://addons/synty_importer/
   ```
2. Open Godot and go to **Project > Project Settings > Plugins**.
3. Enable **Synty Importer & Automator**.
4. In the editor menu:
   - Click **Project > Tools > Import Synty .unitypackage...** to select and unpack any `.unitypackage` file directly.
   - Click **Project > Tools > Fix Synty Asset Packs** to re-map and optimize existing assets at any time.
5. Godot will automatically reload and restart the editor once the automator finishes to apply the new import configurations.

---

## 🛠 Project Structure

```text
godot-synty-importer/
├── requirements.txt
├── README.md
└── addons/synty_importer/           # Canonical Godot 4 Editor Addon & CLI Engine
    ├── plugin.cfg
    ├── plugin.gd
    ├── synty_automator.py           # Self-contained universal automation engine
    └── README.md                    # Addon documentation
```

---

## 📄 License

MIT License. Free for use in personal and commercial Godot projects.
