def pre_find_module_path(_hook_api):
    # The build runtime is complete, but PyInstaller's isolated Tcl probe can
    # fail in non-ASCII source workspaces. Keep normal module search enabled;
    # build-portable.ps1 explicitly bundles Tcl/Tk data and native libraries.
    return None
