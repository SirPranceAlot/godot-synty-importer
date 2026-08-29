import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "addons" / "synty_importer"))
import synty_automator as automator


def test_prefab_renderer_contexts_keep_child_identity_and_slots():
    raw = """--- !u!1 &10
GameObject:
  m_Name: Frame
--- !u!33 &11
MeshFilter:
  m_GameObject: {fileID: 10}
  m_Mesh: {fileID: 1, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
--- !u!23 &12
MeshRenderer:
  m_GameObject: {fileID: 10}
  m_Materials:
  - {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}
  - {fileID: 2100000, guid: cccccccccccccccccccccccccccccccc, type: 2}
--- !u!1 &20
GameObject:
  m_Name: Glass
--- !u!33 &21
MeshFilter:
  m_GameObject: {fileID: 20}
  m_Mesh: {fileID: 2, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
--- !u!23 &22
MeshRenderer:
  m_GameObject: {fileID: 20}
  m_Materials:
  - {fileID: 2100000, guid: dddddddddddddddddddddddddddddddd, type: 2}
"""
    contexts = automator.parse_unity_prefab_renderer_contexts(raw)
    assert [(c["name"], c["materials"]) for c in contexts] == [
        ("Frame", ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccccccccccc"]),
        ("Glass", ["dddddddddddddddddddddddddddddddd"]),
    ]
    assert all(c["mesh_guid"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" for c in contexts)


def test_contextual_overrides_require_authoritative_geometry_material_layer():
    graph = {"model_materials": {"Outer": ["shared"]}}
    contexts = [{"name": "Outer", "materials": ["res://other.tres"]}]
    assert automator.build_contextual_material_overrides(
        graph, contexts, {"shared": "res://fallback.tres"}, {}
    ) == {}


def test_contextual_override_refuses_layer_connection_order_mismatch():
    graph = {
        "model_materials": {"Model": ["first", "second"]},
        "models_by_id": {1: "Model"},
        "model_material_ids_by_id": {1: [102, 101]},
        "geometry_materials_by_id": {
            11: {
                "model_id": 1,
                "mapping": "ByPolygon",
                "reference": "IndexToDirect",
                "material_indices": [0, 1],
                "material_ids": [101, 102],
                "authoritative": True,
            },
        },
        "materials": {101: "first", 102: "second"},
    }
    contexts = [{"name": "Model", "materials": ["res://override.tres"]}]
    assert automator.build_contextual_material_overrides(
        graph,
        contexts,
        {"first": "res://first.tres", "second": "res://second.tres"},
        {"res://override.tres": "res://override.tres"},
    ) == {}


def test_contextual_override_refuses_any_ambiguous_geometry_order():
    graph = {
        "model_materials": {"Model": ["first", "second"]},
        "models_by_id": {1: "Model"},
        "model_material_ids_by_id": {1: [101, 102]},
        "geometry_materials_by_id": {
            11: {"model_id": 1, "authoritative": True, "material_ids": [101, 102]},
            12: {"model_id": 1, "authoritative": True, "material_ids": [102, 101]},
        },
        "materials": {101: "first", 102: "second"},
    }
    contexts = [{"name": "Model", "materials": ["res://alternate.tres", "res://second.tres"]}]
    assert automator.build_contextual_material_overrides(
        graph,
        contexts,
        {"first": "res://first.tres", "second": "res://second.tres"},
        {"res://alternate.tres": "res://alternate.tres", "res://second.tres": "res://second.tres"},
    ) == {}


def test_contextual_override_refuses_conflict_from_unsupported_authoritative_mapping():
    graph = {
        "model_materials": {"Model": ["first", "second"]},
        "models_by_id": {1: "Model"},
        "model_material_ids_by_id": {1: [101, 102]},
        "geometry_materials_by_id": {
            11: {
                "model_id": 1,
                "mapping": "ByPolygon",
                "reference": "Direct",
                "material_indices": [101, 102],
                "material_ids": [101, 102],
                "authoritative": True,
            },
            12: {
                "model_id": 1,
                "mapping": "ByVertex",
                "reference": "Direct",
                "material_indices": [102, 101],
                "material_ids": [102, 101],
                "authoritative": True,
            },
        },
        "materials": {101: "first", 102: "second"},
    }
    contexts = [{"name": "Model", "materials": ["res://first.tres", "res://second.tres"]}]
    assert automator.build_contextual_material_overrides(
        graph,
        contexts,
        {"first": "res://fallback-first.tres", "second": "res://second.tres"},
        {"res://first.tres": "res://first.tres", "res://second.tres": "res://second.tres"},
    ) == {}


def test_model_context_override_maps_shared_fbx_material_to_child_renderer():
    graph = {
        "model_materials": {
            "Outer": ["shared", "brick"],
            "Glass": ["shared"],
        },
        "models_by_id": {1: "Outer", 2: "Glass"},
        "model_material_ids_by_id": {1: [101, 102], 2: [101]},
        "geometry_materials_by_id": {
            11: {
                "model_id": 1,
                "mapping": "ByPolygon",
                "reference": "IndexToDirect",
                "material_indices": [0, 1],
                "authoritative": True,
                "material_ids": [101, 102],
            },
            12: {
                "model_id": 2,
                "mapping": "AllSame",
                "reference": "IndexToDirect",
                "material_indices": [0],
                "authoritative": True,
                "material_ids": [101],
            },
        },
        "materials": {101: "shared", 102: "brick"},
    }
    contexts = [
        {"name": "Outer", "materials": ["frame", "brick"], "mesh_guid": "mesh"},
        {"name": "Glass", "materials": ["glass"], "mesh_guid": "mesh"},
    ]
    result = automator.build_contextual_material_overrides(
        graph, contexts, {"shared": "res://frame.tres", "brick": "res://brick.tres"},
        {"frame": "res://frame.tres", "brick": "res://brick.tres", "glass": "res://glass.tres"},
    )
    assert result == {"Glass": {0: "res://glass.tres"}}


def test_skinned_renderer_context_and_safe_generated_strings():
    raw = """--- !u!1 &10
GameObject:
  m_Name: Bad\"Name
--- !u!137 &11
SkinnedMeshRenderer:
  m_GameObject: {fileID: 10}
  m_Materials:
  - {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}
  m_Mesh: {fileID: 1, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
"""
    contexts = automator.parse_unity_prefab_renderer_contexts(raw)
    assert contexts[0]["name"] == 'Bad"Name'
    assert contexts[0]["mesh_guid"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with tempfile.TemporaryDirectory() as root:
        script = automator.generate_contextual_import_script(
            root, root + "/same.fbx", {'Bad"Name': {0: 'res://safe.tres'}}
        )
        text = Path(root, script.removeprefix("res://")).read_text()
        assert 'Bad\\"Name' in text
        assert 'load("res://safe.tres")' in text


def test_read_fbx_array_property_keeps_uncompressed_values():
    import struct
    raw = b"i" + struct.pack("<III", 3, 0, 12) + struct.pack("<iii", 4, 1, 7)
    values, offset = automator.read_fbx_properties(raw, 0, 1)
    assert values == [[4, 1, 7]]
    assert offset == len(raw)


def test_flat_prefab_arrays_do_not_merge_distinct_renderers():
    raw = """--- !u!23 &12
MeshRenderer:
  m_GameObject: {fileID: 10}
  m_Materials:
  - {fileID: 0}
  - {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}
--- !u!23 &22
MeshRenderer:
  m_GameObject: {fileID: 20}
  m_Materials:
  - {fileID: 2100000, guid: cccccccccccccccccccccccccccccccc, type: 2}
"""
    assert automator.parse_unity_prefab_material_arrays(raw) is None


def test_flat_prefab_arrays_keep_one_renderer_slots():
    raw = """--- !u!23 &12
MeshRenderer:
  m_GameObject: {fileID: 10}
  m_Materials:
  - {fileID: 0}
  - {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}
"""
    assert automator.parse_unity_prefab_material_arrays(raw) == ["", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]


def test_compressed_fbx_array_limit_is_enforced():
    original_limit = automator.MAX_FBX_ARRAY_BYTES
    automator.MAX_FBX_ARRAY_BYTES = 64
    try:
        payload = zlib.compress(b"x" * 65)
        raw = b"b" + struct.pack("<III", 1, 1, len(payload)) + payload
        try:
            automator.read_fbx_properties(raw, 0, 1)
        except struct.error as exc:
            assert "safety limit" in str(exc)
        else:
            raise AssertionError("oversized compressed array was accepted")
    finally:
        automator.MAX_FBX_ARRAY_BYTES = original_limit


def test_malformed_fbx_returns_empty_graph():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root, "broken.fbx")
        header = b"Kaydara FBX Binary  \x00\x1a\x00"
        header += struct.pack("<I", 7400)
        path.write_bytes(header + struct.pack("<IIIB", 40, 1, 1, 0) + b"I")
        assert automator.parse_fbx_graph(str(path)) == {}


def test_package_paths_reject_traversal_and_absolute_paths():
    with tempfile.TemporaryDirectory() as root:
        assert automator.resolve_package_path(root, "../outside.mat") is None
        assert automator.resolve_package_path(root, "/outside.mat") is None
        assert automator.resolve_package_path(root, "C:/outside.mat") is None
        assert automator.package_res_path(root, "../outside.mat") is None
        assert automator.package_res_path(root, "Materials/inside.mat") == (
            "res://Materials/inside.mat"
        )


def test_null_material_path_normalization_keeps_placeholder():
    assert automator.normalize_prefab_material_paths(["", "res://mat.tres"])
    assert automator.normalize_prefab_material_paths(["", "res://mat.tres"]) == [
        "", "res://mat.tres"
    ]


def test_null_material_keeps_positional_slot():
    raw = """--- !u!1 &10
GameObject:
  m_Name: NullSlot
--- !u!33 &11
MeshFilter:
  m_GameObject: {fileID: 10}
  m_Mesh: {fileID: 1, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
--- !u!23 &12
MeshRenderer:
  m_GameObject: {fileID: 10}
  m_Materials:
  - {fileID: 0}
  - {fileID: 2100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}
"""
    contexts = automator.parse_unity_prefab_renderer_contexts(raw)
    assert contexts[0]["materials"] == ["", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]


def test_ambiguous_prefab_contexts_are_skipped():
    graph = {"model_materials": {"Shared": ["shared"]}}
    contexts = [
        {"name": "Shared", "materials": ["res://one.tres"], "prefab_stem": "one"},
        {"name": "Shared", "materials": ["res://two.tres"], "prefab_stem": "two"},
    ]
    assert automator.build_contextual_material_overrides(
        graph, contexts, {"shared": "res://fallback.tres"}, {}
    ) == {}


def test_compressed_fbx_array_property_is_retained():
    import struct
    import zlib
    payload = struct.pack("<iii", 8, 3, 5)
    raw = b"i" + struct.pack("<III", 3, 1, len(zlib.compress(payload))) + zlib.compress(payload)
    values, offset = automator.read_fbx_properties(raw, 0, 1)
    assert values == [[8, 3, 5]]
    assert offset == len(raw)


def test_real_fbx_exposes_geometry_layer_material_records_by_id():
    fbx_path = os.environ.get("SYNTY_TEST_FBX")
    if not fbx_path or not Path(fbx_path).is_file():
        raise unittest.SkipTest(
            "Set SYNTY_TEST_FBX to run the real-FBX integration check"
        )
    graph = automator.parse_fbx_graph(fbx_path)
    assert graph["geometries_by_id"]
    assert graph["geometry_materials_by_id"]
    for geometry_id, record in graph["geometry_materials_by_id"].items():
        assert geometry_id in graph["geometries_by_id"]
        assert record["geometry_id"] == geometry_id
        assert record["model_id"] in graph["models_by_id"]
        assert "mapping" in record and "reference" in record
        assert "material_ids" in record
    assert graph["model_material_ids_by_id"]


def test_incomplete_layer_mapping_does_not_invent_material_ids():
    # Canonical records must not turn an absent mapping/reference into a slot.
    record = automator.resolve_geometry_material_record(
        geometry_id=7, model_id=9, mapping="", reference="", material_indices=[0],
        model_material_ids=[101],
    )
    assert record["authoritative"] is False
    assert record["material_ids"] == []


if __name__ == "__main__":
    skipped = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except unittest.SkipTest as exc:
            skipped += 1
            print(f"skipped {name}: {exc}")
    print(f"contextual material tests passed (skipped={skipped})")
