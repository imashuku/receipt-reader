
import json
import os
from pathlib import Path

OUTPUT_DIR = Path("output")

def _find_sessions() -> list[dict]:
    """output/ 配下の summary.json を探索し、セッション一覧を返す"""
    sessions = []
    print(f"DEBUG: Searching in {OUTPUT_DIR.resolve()}")
    if not OUTPUT_DIR.exists():
        print("OUTPUT_DIR does not exist!")
        return []
        
    for summary_path in sorted(OUTPUT_DIR.glob("*/summary.json")):
        print(f"DEBUG: Found {summary_path}")
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "dir": summary_path.parent.name,
                "file": data.get("file", ""),
                "total": data.get("total_receipts", 0),
                "valid": data.get("valid_count", 0),
                "invalid": data.get("invalid_count", 0),
                "path": str(summary_path),
            })
            print(f"DEBUG: Loaded {summary_path}")
        except Exception as e:
            print(f"DEBUG: Error loading {summary_path}: {e}")
            pass
    return sessions

if __name__ == "__main__":
    sessions = _find_sessions()
    print("-" * 20)
    print(f"Found {len(sessions)} sessions:")
    for s in sessions:
        print(f" - {s['file']} ({s['dir']})")
