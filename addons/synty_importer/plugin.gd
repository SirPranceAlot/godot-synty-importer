@tool
extends EditorPlugin

const FIX_TOOL_NAME: String = "Fix Synty Asset Packs"
const IMPORT_TOOL_NAME: String = "Import Synty .unitypackage..."

var file_dialog: EditorFileDialog

func _enter_tree() -> void:
	add_tool_menu_item(FIX_TOOL_NAME, Callable(self, "_on_fix_synty_assets"))
	add_tool_menu_item(IMPORT_TOOL_NAME, Callable(self, "_on_import_unitypackage_dialog"))

func _exit_tree() -> void:
	remove_tool_menu_item(FIX_TOOL_NAME)
	remove_tool_menu_item(IMPORT_TOOL_NAME)
	if is_instance_valid(file_dialog):
		file_dialog.queue_free()

func _on_fix_synty_assets() -> void:
	_run_automator([])

func _on_import_unitypackage_dialog() -> void:
	if not is_instance_valid(file_dialog):
		file_dialog = EditorFileDialog.new()
		file_dialog.file_mode = EditorFileDialog.FILE_MODE_OPEN_FILE
		file_dialog.access = EditorFileDialog.ACCESS_FILESYSTEM
		file_dialog.add_filter("*.unitypackage", "Unity Package Archive")
		file_dialog.file_selected.connect(_on_package_file_selected)
		EditorInterface.get_base_control().add_child(file_dialog)
	file_dialog.popup_file_dialog()

func _on_package_file_selected(path: String) -> void:
	print("Selected package: ", path)
	_run_automator(["--package", path])

func _run_automator(extra_args: Array) -> void:
	print("--- Running Synty Asset Automator ---")
	var project_path: String = ProjectSettings.globalize_path("res://")
	var script_path: String = project_path.path_join("addons/synty_importer/synty_automator.py")
	
	if not FileAccess.file_exists(script_path):
		script_path = project_path.path_join("synty_automator.py")
	
	if FileAccess.file_exists(script_path):
		var args: Array = [script_path, "--path", project_path]
		args.append_array(extra_args)
		
		var output: Array = []
		var exit_code: int = OS.execute("python3", args, output, true)
		print("".join(output))
		if exit_code == 0:
			print("Synty assets configured successfully! Reloading project...")
			EditorInterface.restart_editor(true)
		else:
			printerr("Synty automator finished with exit code: ", exit_code)
	else:
		printerr("synty_automator.py not found at: ", script_path)
