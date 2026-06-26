import re
from pathlib import Path


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def repo_root():
    return Path(__file__).resolve().parents[1]


def validate_safe_id(value, name):
    if not SAFE_ID_RE.fullmatch(value or ""):
        raise SystemExit(
            f"Invalid {name}: {value!r}. Use 1-64 characters: letters, numbers, '_', '.', '-'."
        )
    return value


def block_sort_key(path):
    suffix = path.stem.removeprefix("Block")
    return (0, int(suffix)) if suffix.isdigit() else (1, path.stem)


def available_blocks(root, require_screenshot=True):
    block_dir = root / "testcase" / "asap7" / "block"
    blocks = []
    for layout_path in sorted((block_dir / "layout_script").glob("Block*.py"), key=block_sort_key):
        case_name = layout_path.stem
        required_paths = [
            layout_path,
            block_dir / "drc_report" / f"{case_name}.drc.json",
            block_dir / "connectivity" / f"{case_name}.json",
        ]
        if require_screenshot:
            required_paths.append(block_dir / "layout_screenshot" / case_name / f"{case_name}.png")
        if all(path.is_file() for path in required_paths):
            blocks.append(case_name)
    return blocks
