from importlib import import_module
from pathlib import Path
import subprocess
import sys


def test_package_imports_from_project_source() -> None:
    package = import_module("marine_forecast")

    # A regular package should resolve to a concrete source file.
    assert package.__file__ is not None

    project_root = Path(__file__).resolve().parents[1]
    expected_path = (.
        project_root / "src" / "marine_forecast" / "__init__.py"
    )

    # Confirm the editable installation points to this checkout.
    assert Path(package.__file__).resolve() == expected_path


def test_package_imports_outside_project(tmp_path: Path) -> None:
    # Exclude the current directory and PYTHONPATH to verify the installation itself.
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import marine_forecast; print(marine_forecast.__file__)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    # Report the child process error before interpreting its output as a path.
    assert completed.returncode == 0, completed.stderr

    project_root = Path(__file__).resolve().parents[1]
    expected_path = (
        project_root / "src" / "marine_forecast" / "__init__.py"
    )
    assert Path(completed.stdout.strip()).resolve() == expected_path
