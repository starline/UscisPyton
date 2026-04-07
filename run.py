"""
Лаунчер скриптов: python run.py <имя> [аргументы...]
Пример: python run.py uscis_pdf_download
Список: python run.py --list
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"


def list_scripts() -> None:
    if not SCRIPTS.is_dir():
        print("Папка scripts не найдена.")
        return
    names = sorted(p.stem for p in SCRIPTS.glob("*.py") if p.name != "__init__.py")
    if not names:
        print("Нет скриптов в scripts/.")
        return
    print("Доступные скрипты:")
    for n in names:
        print(f"  {n}")


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        print()
        list_scripts()
        sys.exit(0 if argv else 1)
    if argv[0] in ("-l", "--list"):
        list_scripts()
        sys.exit(0)

    name = argv[0]
    script = SCRIPTS / f"{name}.py"
    if not script.is_file():
        print(f"Не найден: {script}")
        list_scripts()
        sys.exit(1)

    cmd = [sys.executable, str(script), *argv[1:]]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
