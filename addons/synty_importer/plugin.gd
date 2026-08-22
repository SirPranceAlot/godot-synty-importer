@tool
extends EditorPlugin

const TOOL_NAME: String = "Fix Synty Asset Packs"

func _enter_tree() -> void:
	add_tool_menu_item(TOOL_NAME, Callable(self, "_on_fix_synty_assets"))

func _exit_tree() -> void:
	remove_tool_menu_item(TOOL_NAME)

func _on_fix_synty_assets() -> void:
	print("--- Running Synty Asset Automator ---")
	var project_path: String = ProjectSettings.globalize_path("res://")
	var script_path: String = project_path.path_join("addons/synty_importer/synty_automator.py")
	
	if not FileAccess.file_exists(script_path):
		script_path = project_path.path_join("synty_automator.py")
	
	if FileAccess.file_exists(script_path):
		var output: Array = []
		var exit_code: int = OS.execute("python3", [script_path, "--path", project_path], output, true)
		print("".join(output))
		if exit_code == 0:
			print("Synty assets configured successfully! Reloading project...")
			EditorInterface.restart_editor(true)
		else:
			printerr("Synty automator finished with exit code: ", exit_code)
	else:
		printerr("synty_automator.py not found at: ", script_path)
