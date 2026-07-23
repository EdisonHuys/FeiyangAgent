import PyInstaller.__main__
import subprocess
import os
import sys

def build():
    print("=== STARTING DESKTOP APP COMPILATION ===")
    
    # 1. Compile frontend assets
    print("1. Compiling React frontend via npm...")
    try:
        subprocess.run("npm run build", shell=True, cwd="frontend", check=True)
        print("Frontend compilation successful.")
    except Exception as e:
        print(f"Error compiling React frontend: {e}")
        sys.exit(1)

    # 2. Run PyInstaller
    print("2. Packaging application with PyInstaller...")
    # On macOS/Linux, PyInstaller uses ':' for path separation in --add-data.
    # On Windows it uses ';'. Since the user is on Mac, we use ':'.
    separator = ':'
    
    pyinstaller_args = [
        'main.py',                         # Entry point
        '--name=FeiyangAgent',             # App name
        '--icon=assets/icon.icns',         # macOS App Icon
        '--noconfirm',                     # Overwrite output directory without confirmation
        '--windowed',                      # Windowed / GUI mode (no console popup)
        '--noconsole',                     # Don't show shell console
        '--clean',                         # Clean cache before build
        f'--add-data=frontend/dist{separator}frontend/dist', # Bundle React static assets
        '--collect-all=ccxt',              # Ensure all CCXT exchange definitions are collected
        '--collect-all=pandas_ta',         # Ensure all TA functions are collected
        '--collect-all=fastapi',           # Collect all FastAPI dependencies
        '--collect-all=uvicorn',           # Collect all uvicorn packages
        '--collect-all=webview',           # Collect all pywebview assets
    ]
    
    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n=== COMPILATION SUCCESSFUL ===")
        print("Your desktop application is ready at: dist/FeiyangAgent.app")
    except Exception as e:
        print(f"Error compiling bundle: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build()
