"""
Cross-platform build script for FeiyangAgent desktop app.
Supports macOS (.app bundle) and Windows (.exe via PyInstaller --onedir).

Usage:
    python build_app.py          # Auto-detect current platform
    python build_app.py --macos  # Force macOS build
    python build_app.py --win    # Force Windows build (cross-compile not supported, run on Windows)
"""
import PyInstaller.__main__
import subprocess
import os
import sys
import platform
import argparse


def detect_platform():
    """Detect the current build platform."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    else:
        return "linux"


def build_frontend():
    """Compile the React frontend via npm."""
    print("1. Compiling React frontend via npm...")
    try:
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.run(f"{npm_cmd} run build", shell=True, cwd="frontend", check=True)
        print("   Frontend compilation successful.")
    except FileNotFoundError:
        print("   ERROR: npm not found. Please install Node.js 18+ first.")
        print("   Download: https://nodejs.org/")
        sys.exit(1)
    except Exception as e:
        print(f"   ERROR compiling React frontend: {e}")
        sys.exit(1)


import shutil

def sync_prompt_to_dist():
    """Sync root feiyang_prompt.txt to dist/ directory so packaged app uses updated prompt."""
    root_prompt = "feiyang_prompt.txt"
    if os.path.exists(root_prompt):
        dist_dir = "dist"
        os.makedirs(dist_dir, exist_ok=True)
        dst_path = os.path.join(dist_dir, "feiyang_prompt.txt")
        shutil.copy2(root_prompt, dst_path)
        print(f"   [Sync] Successfully synchronized {root_prompt} -> {dst_path}")

        win_onedir = os.path.join(dist_dir, "FeiyangAgent")
        if os.path.isdir(win_onedir):
            win_dst = os.path.join(win_onedir, "feiyang_prompt.txt")
            shutil.copy2(root_prompt, win_dst)
            print(f"   [Sync] Successfully synchronized {root_prompt} -> {win_dst}")


def build_macos():
    """Build macOS .app bundle."""
    print("2. Packaging macOS application with PyInstaller...")

    icon_path = "assets/icon.icns"
    if not os.path.exists(icon_path):
        print(f"   WARNING: Icon file {icon_path} not found, building without icon.")
        icon_args = []
    else:
        icon_args = [f"--icon={icon_path}"]

    pyinstaller_args = [
        'main.py',
        '--name=FeiyangAgent',
        '--noconfirm',
        '--windowed',
        '--noconsole',
        '--clean',
        '--add-data=frontend/dist:frontend/dist',
        '--collect-all=ccxt',
        '--collect-all=pandas_ta',
        '--collect-all=fastapi',
        '--collect-all=uvicorn',
        '--collect-all=webview',
    ] + icon_args

    try:
        PyInstaller.__main__.run(pyinstaller_args)
        sync_prompt_to_dist()
        print("\n=== macOS BUILD SUCCESSFUL ===")
        print("Your application is ready at: dist/FeiyangAgent.app")
        print("To run: open dist/FeiyangAgent.app")
    except Exception as e:
        print(f"ERROR compiling bundle: {e}")
        sys.exit(1)


def build_windows():
    """Build Windows .exe application (onedir mode for faster startup)."""
    print("2. Packaging Windows application with PyInstaller...")

    icon_path = "assets/icon.ico"
    if not os.path.exists(icon_path):
        # Try to find any .ico file in assets
        if os.path.isdir("assets"):
            ico_files = [f for f in os.listdir("assets") if f.endswith(".ico")]
            if ico_files:
                icon_path = os.path.join("assets", ico_files[0])
            else:
                print("   WARNING: No .ico icon found in assets/, building without icon.")
                icon_path = None
        else:
            print("   WARNING: assets/ directory not found, building without icon.")
            icon_path = None

    icon_args = [f"--icon={icon_path}"] if icon_path else []

    # Windows uses ';' as path separator for --add-data
    pyinstaller_args = [
        'main.py',
        '--name=FeiyangAgent',
        '--noconfirm',
        '--windowed',
        '--noconsole',
        '--clean',
        '--onedir',  # Directory mode: faster startup than --onefile, easier to debug
        '--add-data=frontend/dist;frontend/dist',
        '--collect-all=ccxt',
        '--collect-all=pandas_ta',
        '--collect-all=fastapi',
        '--collect-all=uvicorn',
        '--collect-all=webview',
        # Windows-specific: include WebView2 runtime loader
        '--collect-binaries=webview',
        # Hidden imports that PyInstaller sometimes misses on Windows
        '--hidden-import=clr',
        '--hidden-import=webview.platforms.edgechromium',
        '--hidden-import=webview.platforms.winforms',
    ] + icon_args

    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n=== WINDOWS BUILD SUCCESSFUL ===")
        print("Your application is ready at: dist/FeiyangAgent/FeiyangAgent.exe")
        print("To distribute: zip the entire dist/FeiyangAgent/ folder")
        print("\nNOTE: Windows requires Microsoft Edge WebView2 Runtime.")
        print("Most Windows 10/11 machines have it pre-installed.")
        print("If not, download from: https://developer.microsoft.com/en-us/microsoft-edge/webview2/")
    except Exception as e:
        print(f"ERROR compiling bundle: {e}")
        sys.exit(1)


def build_linux():
    """Build Linux application (experimental)."""
    print("2. Packaging Linux application with PyInstaller...")

    pyinstaller_args = [
        'main.py',
        '--name=FeiyangAgent',
        '--noconfirm',
        '--windowed',
        '--noconsole',
        '--clean',
        '--onedir',
        '--add-data=frontend/dist:frontend/dist',
        '--collect-all=ccxt',
        '--collect-all=pandas_ta',
        '--collect-all=fastapi',
        '--collect-all=uvicorn',
        '--collect-all=webview',
        '--hidden-import=webview.platforms.gtk',
        '--hidden-import=webview.platforms.qt',
    ]

    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n=== LINUX BUILD SUCCESSFUL ===")
        print("Your application is ready at: dist/FeiyangAgent/FeiyangAgent")
        print("Requires: python3-gi, gir1.2-webkit2-4.0 (GTK WebKit)")
    except Exception as e:
        print(f"ERROR compiling bundle: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="FeiyangAgent Cross-Platform Build Script")
    parser.add_argument("--macos", action="store_true", help="Force macOS build")
    parser.add_argument("--win", "--windows", action="store_true", dest="windows", help="Force Windows build")
    parser.add_argument("--linux", action="store_true", help="Force Linux build")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip npm build (use existing frontend/dist)")
    args = parser.parse_args()

    print("=" * 50)
    print("  FeiyangAgent Desktop App Builder")
    print("=" * 50)

    # Determine target platform
    if args.macos:
        target = "macos"
    elif args.windows:
        target = "windows"
    elif args.linux:
        target = "linux"
    else:
        target = detect_platform()

    print(f"\nTarget platform: {target}")
    print(f"Python: {sys.version}")
    print(f"PyInstaller: {PyInstaller.__version__}")
    print()

    # Step 1: Build frontend
    if not args.skip_frontend:
        build_frontend()
    else:
        if not os.path.exists("frontend/dist/index.html"):
            print("ERROR: --skip-frontend specified but frontend/dist/index.html not found!")
            print("Run 'cd frontend && npm install && npm run build' first.")
            sys.exit(1)
        print("1. Skipping frontend build (using existing frontend/dist)")

    # Step 2: Platform-specific PyInstaller build
    if target == "macos":
        build_macos()
    elif target == "windows":
        build_windows()
    elif target == "linux":
        build_linux()
    else:
        print(f"ERROR: Unsupported platform '{target}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
