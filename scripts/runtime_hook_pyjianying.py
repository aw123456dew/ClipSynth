"""
Runtime hook to fix pyJianYingDraft asset path issue in PyInstaller bundles.

This hook patches the pyJianYingDraft.assets module to correctly locate
asset files when running inside a PyInstaller bundle.
"""
import sys
import os
from pathlib import Path


def patch_pyjianying_assets():
    """Patch pyJianYingDraft.assets module to use correct asset path."""
    if not getattr(sys, 'frozen', False):
        # Not running in a PyInstaller bundle, nothing to do
        return
    
    try:
        import pyJianYingDraft.assets as assets_module
        
        # Get the actual assets directory from sys._MEIPASS
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            actual_assets_dir = Path(meipass) / 'pyJianYingDraft' / 'assets'
            
            # Patch the ASSETS_DIR in the assets module
            if actual_assets_dir.exists():
                original_dir = assets_module.ASSETS_DIR
                assets_module.ASSETS_DIR = actual_assets_dir
                print(f"[Runtime Hook] Patched pyJianYingDraft.assets.ASSETS_DIR: {original_dir} -> {actual_assets_dir}")
            else:
                print(f"[Runtime Hook] Warning: Assets directory not found at {actual_assets_dir}")
    except Exception as e:
        print(f"[Runtime Hook] Failed to patch pyJianYingDraft.assets: {e}")


patch_pyjianying_assets()
