"""Repository paths shared by source entry points and editable installs."""
from pathlib import Path


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "daimon_control").is_dir():
            return parent
    # A non-editable installation can use --config; otherwise look in cwd.
    return Path.cwd()


PROJECT_ROOT = project_root()
DEFAULT_CONFIG = PROJECT_ROOT / "gripper_server_config.json"
