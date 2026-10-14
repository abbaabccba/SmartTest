import os
import subprocess
import sys
from pathlib import Path


PACKAGES = (
    "PySide6==6.7.2", "pywin32==311; sys_platform == 'win32'", "keyring==25.7.0; sys_platform != 'win32'",
    "SecretStorage==3.3.3; sys_platform == 'linux'", "nuitka==2.3.2", "PyInstaller==6.8.0",
    "qasync==0.27.1", "aiohttp==3.11.8", "qrcode==7.4.2", "pillow==10.4.0",
    "numpy==2.1.3", "matplotlib==3.10.5", "opencv-python==4.10.0.84",
    "keyboard==0.13.5", "PyOpenGL==3.1.7", "py7zr==0.22.0", "pyserial==3.5",
    "ldap3==2.9.1", "pycryptodome==3.23.0", "openpyxl==3.1.5",
    "atlassian-python-api==4.0.7", "tzdata==2025.2", "Markdown==3.10.2",
    "playwright==1.54.0", "pytest==8.4.1",
    "uiautomator2==3.5.2",
    "xlrd==2.0.2",
    "nodeenv==1.9.1",
)

NODE_VERSION = "22.14.0"


def initialize_environment(root=None, runner=subprocess.run):
    root = Path(root or Path(__file__).resolve().parents[3])
    venv = root / ".venv"
    bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
    python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
    node = bin_dir / ("node.exe" if sys.platform == "win32" else "node")
    npm = bin_dir / ("npm.cmd" if sys.platform == "win32" else "npm")
    if not python.is_file():
        runner([sys.executable, "-m", "venv", str(venv)], check=True)
    runner([str(python), "-m", "pip", "install", *PACKAGES], check=True)
    runner([str(python), "-m", "playwright", "install", "chromium"], check=True)
    runner(
        [
            str(python), "-m", "pip", "install",
            "-r", str(root / "web" / "backend" / "requirements.txt"),
            "-r", str(root / "web" / "backend" / "requirements-dev.txt"),
        ],
        check=True,
    )
    if not node.is_file() or not npm.is_file():
        runner(
            [str(python), "-m", "nodeenv", "-p", f"--node={NODE_VERSION}", "--prebuilt"],
            check=True,
        )
    npm_env = os.environ.copy()
    npm_env["PATH"] = os.pathsep.join(part for part in (str(bin_dir), npm_env.get("PATH", "")) if part)
    runner(
        [str(npm), "ci"],
        cwd=root / "web" / "frontend",
        env=npm_env,
        check=True,
    )


if __name__ == "__main__":
    initialize_environment()
