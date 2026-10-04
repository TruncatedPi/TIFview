"""Windowed executable entry point; packaging checks use the same viewer."""
import json
from pathlib import Path
import sys
import traceback


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--smoke-test":
        try:
            from tools.package_smoke import run
            result = run(sys.argv[2], sys.argv[3])
        except Exception:
            # Windowed builds have no stderr. Preserve startup failures for CI.
            report = Path(sys.argv[3]).resolve()
            if report != Path(sys.argv[2]).resolve() and not report.exists():
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps({"passed": False, "error": traceback.format_exc()},
                                            indent=2), encoding="utf-8")
            result = 1
        raise SystemExit(result)
    from tifview.__main__ import main
    raise SystemExit(main())
