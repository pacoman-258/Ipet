from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.skills.manager import SkillManager  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/quick_validate.py <skill_directory>")
        return 1

    target = Path(argv[1])
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()

    try:
        manager = SkillManager(REPO_ROOT)
        manager.validate_skill_dir(target, source_type="imported")
    except Exception as exc:
        print(f"Skill validation failed: {exc}")
        return 1

    print("Skill is valid!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
