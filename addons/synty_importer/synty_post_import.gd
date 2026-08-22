@tool
extends EditorScenePostImport

var albedo_dict: Dictionary = {}
var normal_dict: Dictionary = {}
var emissive_dict: Dictionary = {}
var loaded_texture_cache: Dictionary = {}

var default_albedo_path: String = ""
var default_normal_path: String = ""
var default_emissive_path: String = ""
var current_pack_path: String = ""

func _post_import(scene: Node) -> Object:
	var source_path: String = get_source_file()
	if source_path == "":
		return scene
	
	var pack_path: String = get_pack_root(source_path)
	if pack_path != current_pack_path:
		current_pack_path = pack_path
		setup_pack_textures(pack_path)
	
	process_node(scene, source_path)
	return scene

func get_pack_root(file_path: String) -> String:
	var parts: PackedStringArray = file_path.split("/")
	var synty_idx: int = -1
	for i in range(parts.size()):
		if parts[i].to_lower() == "synty" and i + 1 < parts.size():
			synty_idx = i + 1
			break
	if synty_idx != -1:
		return "/".join(parts.slice(0, synty_idx + 1))
	return file_path.get_base_dir()

func setup_pack_textures(pack_path: String) -> void:
	albedo_dict.clear()
	normal_dict.clear()
	emissive_dict.clear()
	loaded_texture_cache.clear()
	default_albedo_path = ""
	default_normal_path = ""
	default_emissive_path = ""
	
	var generic_dir: String = "res://Assets/Synty/PolygonGeneric/Textures"
	if DirAccess.dir_exists_absolute(generic_dir):
		index_textures(generic_dir)
	
	var pack_tex_dir: String = pack_path.path_join("Textures")
	if DirAccess.dir_exists_absolute(pack_tex_dir):
		index_textures(pack_tex_dir)
	
	if "sidekick" in pack_path.to_lower():
		index_textures(pack_path)
	
	var pack_name: String = pack_path.get_file().to_lower()
	for key in albedo_dict:
		if (key.begins_with(pack_name) or "01_a" in key or "colormap" in key) and default_albedo_path == "":
			default_albedo_path = albedo_dict[key]
			break

func index_textures(dir_path: String) -> void:
	var dir: DirAccess = DirAccess.open(dir_path)
	if not dir:
		return
	
	dir.list_dir_begin()
	var file_name: String = dir.get_next()
	while file_name != "":
		if dir.current_is_dir() and not file_name.begins_with("."):
			index_textures(dir_path.path_join(file_name))
		elif not file_name.ends_with(".import") and not file_name.ends_with(".failed_import"):
			var ext: String = file_name.get_extension().to_lower()
			if ext in ["png", "tga", "jpg", "jpeg", "webp"]:
				var full_path: String = dir_path.path_join(file_name)
				var stem: String = file_name.get_basename().to_lower()
				
				if "/normals/" in full_path.to_lower() or stem.ends_with("_normal") or stem.ends_with("_normals"):
					normal_dict[stem] = full_path
					var clean_stem: String = stem.replace("_normals", "").replace("_normal", "")
					normal_dict[clean_stem] = full_path
				elif "/emissive/" in full_path.to_lower() or stem.ends_with("_emissive"):
					emissive_dict[stem] = full_path
					var clean_stem: String = stem.replace("_emissive", "")
					emissive_dict[clean_stem] = full_path
				else:
					albedo_dict[stem] = full_path
		file_name = dir.get_next()
	dir.list_dir_end()

func get_cached_texture(tex_path: String) -> Texture2D:
	if tex_path == "":
		return null
	if loaded_texture_cache.has(tex_path):
		return loaded_texture_cache[tex_path]
	var tex: Resource = ResourceLoader.load(tex_path, "Texture2D")
	if tex and tex is Texture2D:
		loaded_texture_cache[tex_path] = tex
		return tex as Texture2D
	return null

func process_node(node: Node, source_path: String) -> void:
	var node_name: String = node.name.to_lower()
	var lower_src: String = source_path.to_lower()
	if "collision" in node_name or "convex" in node_name or "/collision/" in lower_src:
		for child in node.get_children():
			process_node(child, source_path)
		return
	
	if node is MeshInstance3D:
		var mi: MeshInstance3D = node as MeshInstance3D
		var mesh_obj: Object = mi.mesh
		var lookup_key: String = source_path.get_file().get_basename().to_lower()
		lookup_key = lookup_key.replace(".fbx", "").replace(".obj", "")
		
		if mesh_obj:
			var surface_count: int = 0
			if mesh_obj is ImporterMesh:
				surface_count = (mesh_obj as ImporterMesh).get_surface_count()
			elif mesh_obj is Mesh:
				surface_count = (mesh_obj as Mesh).get_surface_count()
			
			for i in range(surface_count):
				var has_tangents: bool = false
				if mesh_obj is ImporterMesh:
					has_tangents = bool((mesh_obj as ImporterMesh).get_surface_format(i) & Mesh.ARRAY_FORMAT_TANGENT)
				elif mesh_obj is Mesh:
					has_tangents = bool((mesh_obj as Mesh).surface_get_format(i) & Mesh.ARRAY_FORMAT_TANGENT)
				
				var mat: StandardMaterial3D = StandardMaterial3D.new()
				configure_material(mat, lookup_key, source_path, has_tangents)
				
				if mesh_obj is ImporterMesh:
					(mesh_obj as ImporterMesh).set_surface_material(i, mat)
				mi.set_surface_override_material(i, mat)
	
	for child in node.get_children():
		process_node(child, source_path)

func configure_material(mat: StandardMaterial3D, lookup_key: String, source_path: String, has_tangents: bool) -> void:
	var albedo_path: String = find_texture_path(lookup_key, albedo_dict)
	if albedo_path == "" and default_albedo_path != "":
		albedo_path = default_albedo_path
	
	if albedo_path != "":
		var tex: Texture2D = get_cached_texture(albedo_path)
		if tex:
			mat.albedo_texture = tex
	
	if has_tangents:
		var normal_path: String = find_texture_path(lookup_key, normal_dict)
		if normal_path == "" and default_normal_path != "":
			normal_path = default_normal_path
		if normal_path != "":
			var n_tex: Texture2D = get_cached_texture(normal_path)
			if n_tex:
				mat.normal_enabled = true
				mat.normal_texture = n_tex
	else:
		mat.normal_enabled = false
	
	var emissive_path: String = find_texture_path(lookup_key, emissive_dict)
	if emissive_path == "" and default_emissive_path != "":
		emissive_path = default_emissive_path
	if emissive_path != "":
		var e_tex: Texture2D = get_cached_texture(emissive_path)
		if e_tex:
			mat.emission_enabled = true
			mat.emission = Color.WHITE
			mat.emission_energy_multiplier = 1.0
			mat.emission_texture = e_tex
	
	mat.roughness = 0.8
	
	var lower_src: String = source_path.to_lower()
	if "/graffiti/" in lower_src or lookup_key.begins_with("graffiti"):
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_ALWAYS
	elif "/holograms/" in lower_src or lookup_key.begins_with("hologram"):
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	elif "/fx/" in lower_src or lookup_key.begins_with("fx_"):
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	elif "glass" in lookup_key:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.albedo_color = Color(1.0, 1.0, 1.0, 0.4)
	else:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
		mat.cull_mode = BaseMaterial3D.CULL_BACK
		mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_OPAQUE_ONLY

func find_texture_path(key: String, dict: Dictionary) -> String:
	if dict.has(key):
		return dict[key]
	
	if key.begins_with("billboard_"):
		for alt in ["billboard_01", "billboard_02", "billboard_01_damaged", "billboard_02_damaged", "billboard_03"]:
			if key.begins_with(alt) and dict.has(alt):
				return dict[alt]
	
	if key.begins_with("wall_"):
		var base_wall: String = key.replace("_alt", "").replace("_01", "")
		if dict.has(base_wall):
			return dict[base_wall]
	
	return ""
