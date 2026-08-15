"""Создаёт ярлык TextHelper на рабочем столе.

Запустить один раз: python create_shortcut.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import texthelper

BASE_DIR = Path(__file__).resolve().parent
SCRIPT = BASE_DIR / "texthelper.py"


def python_launcher():
    """pythonw.exe запускает приложение без окна консоли."""
    launcher = Path(sys.executable).with_name("pythonw.exe")
    return launcher if launcher.exists() else Path(sys.executable)


def desktop_dir():
    """Рабочий стол через реестр: у OneDrive он не в профиле пользователя."""
    command = (
        "[Environment]::GetFolderPath("
        "[Environment+SpecialFolder]::DesktopDirectory)"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, encoding="utf-8",
    )
    path = Path(result.stdout.strip())
    return path if path.is_dir() else Path.home() / "Desktop"


def create_shortcut():
    icon = texthelper.save_ico()
    target = desktop_dir() / "TextHelper.lnk"

    script = f"""
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut('{target}')
$link.TargetPath = '{python_launcher()}'
$link.Arguments = '"{SCRIPT}"'
$link.WorkingDirectory = '{BASE_DIR}'
$link.IconLocation = '{icon}'
$link.Description = 'TextHelper — обработка текста через Claude'
$link.Save()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit(f"Не удалось создать ярлык:\n{result.stderr}")

    print(f"Иконка:  {icon}")
    print(f"Ярлык:   {target}")
    print("Готово. Ярлык TextHelper на рабочем столе — двойной клик запускает приложение.")


if __name__ == "__main__":
    create_shortcut()
