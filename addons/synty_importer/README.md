# Synty Importer & Automator for Godot 4

A Godot 4 editor plugin and automation engine to seamlessly import, repair, map, and optimize **Synty Studios** 3D asset packs.

---

## 📋 Prerequisites

The plugin uses an embedded Python engine (`synty_automator.py`) to process asset files in parallel.

Ensure your system has:
1. **Python 3.8+** installed and available in your system `PATH` (`python` or `python3`).
2. **Pillow** image library:
   ```bash
   pip install Pillow
   ```

---

## 🚀 Installation

1. Copy the `addons/synty_importer` directory into your Godot project's `addons/` folder:
   ```text
   res://addons/synty_importer/
   ```
2. Open Godot 4 and navigate to **Project > Project Settings > Plugins**.
3. Check the **Enable** box next to **Synty Importer & Automator**.

---

## 🎮 How to Use

### 1. Import a `.unitypackage` directly
1. In the top editor menu, click **Project > Tools > Import Synty .unitypackage...**.
2. Select your Synty `.unitypackage` file in the file dialog.
3. The automator will extract the package, configure textures, assign material slots, set up character rigs, and synchronize resource UIDs.
4. Godot will automatically reload/restart the project once configuration is complete.

### 2. Fix / Re-map Existing Assets
If you already extracted or moved Synty assets in your project:
1. In the top editor menu, click **Project > Tools > Fix Synty Asset Packs**.
2. The automator will scan and repair all FBX models, textures, triplanar materials, character rigs, and scene UIDs.
3. Godot will automatically reload/restart the project.

---

## ⚙️ What the Plugin Does

- **Universal Synty Pack Compatibility**: Fully dynamic heuristics support any Synty Studios pack (Polygon, Simple, Sidekick, Fantasy, Sci-Fi, Dungeon, City, Apocalypse, Western, Samurai, Nature, etc.).
- **Dynamic Missing Texture & PSD Resolver**: Dynamically scans all scenes, materials, and FBX binaries, auto-generating neutral and atlas texture stubs so missing embedded paths never crash or trigger 404 image errors.
- **Automated StandardMaterial3D Generation**: Automatically generates Godot 4 `StandardMaterial3D` resources for any asset pack that only includes raw FBX meshes and textures.
- **Deep FBX Material Slot Mapping**: Resolves internal Maya/3ds Max material slots (e.g., `MAT_01A`, `Scifi_1a9`, `lambert`) to Godot `.mat.tres` materials using a 4-tier semantic fallback system.
- **Selective Multi-Character Visibility**: Configures multi-mesh character prefabs (e.g. `Characters.fbx`) so only the matching character variant is visible.
- **Texture Format Normalization**: Detects and fixes misnamed image formats and corrects sRGB / normal map compression flags in `.import` files.
- **Triplanar Material Configuration**: Configures world-triplanar UV projection and scaling on modular building materials.
- **UID Synchronization**: Updates all scene and prefab UID references to prevent invalid UID console warnings.
