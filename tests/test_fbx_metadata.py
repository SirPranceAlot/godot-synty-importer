import os
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "addons" / "synty_importer"))
import synty_automator as automator


REAL_FBX_PATH = os.environ.get("SYNTY_TEST_FBX")
REAL_FBX = Path(REAL_FBX_PATH) if REAL_FBX_PATH else Path()


def test_read_fbx_integer_array_decodes_uncompressed_values():
    raw = b"i" + struct.pack("<III", 3, 0, 12) + struct.pack("<iii", 4, 1, 7)
    values, offset = automator.read_fbx_properties(raw, 0, 1)
    assert values == [[4, 1, 7]]
    assert offset == len(raw)


def test_read_fbx_integer_array_decodes_deflate_values():
    payload = zlib.compress(struct.pack("<iiii", 2, 5, 11, 19))
    raw = b"i" + struct.pack("<III", 4, 1, len(payload)) + payload
    values, offset = automator.read_fbx_properties(raw, 0, 1)
    assert values == [[2, 5, 11, 19]]
    assert offset == len(raw)


def test_nested_geometry_layer_metadata_is_collected_into_canonical_record():
    geometry = (
        "Geometry", [42], [
            ("Layer", [], [
                ("LayerElementMaterial", [0], [
                    ("Metadata", [], [
                        ("MappingInformationType", ["ByPolygon"], []),
                        ("ReferenceInformationType", ["IndexToDirect"], []),
                    ]),
                    ("Materials", [[0, 1, 2]], []),
                ]),
            ]),
        ],
    )
    records = automator.extract_geometry_material_layers(geometry)
    assert records == {
        42: {
            "geometry_id": 42,
            "model_id": None,
            "mapping": "ByPolygon",
            "reference": "IndexToDirect",
            "material_indices": [0, 1, 2],
            "authoritative": True,
        }
    }


def test_real_fbx_exposes_geometry_material_layer_records_by_id():
    if not REAL_FBX_PATH or not REAL_FBX.is_file():
        raise unittest.SkipTest(
            "Set SYNTY_TEST_FBX to run the real-FBX integration check"
        )
    graph = automator.parse_fbx_graph(str(REAL_FBX))
    assert graph["geometry_material_layers"]
    assert all(isinstance(key, int) for key in graph["geometry_material_layers"])
    layer = next(iter(graph["geometry_material_layers"].values()))
    assert layer["mapping"] == "ByPolygon"
    assert layer["reference"] == "IndexToDirect"
    assert layer["material_indices"]
    assert layer["geometry_id"] in graph["geometry_model_ids"]
    assert layer["authoritative"] is True
    assert graph["geometry_model_ids"][layer["geometry_id"]] in graph["models"]
    assert graph["geometry_material_layers"] is graph["geometry_materials_by_id"]


if __name__ == "__main__":
    test_read_fbx_integer_array_decodes_uncompressed_values()
    test_read_fbx_integer_array_decodes_deflate_values()
    try:
        test_real_fbx_exposes_geometry_material_layer_records_by_id()
    except unittest.SkipTest as exc:
        print(f"skipped real-FBX integration check: {exc}")
    print("fbx metadata tests passed")
