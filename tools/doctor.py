#!/usr/bin/env python3
"""Read-only check of this Mac's CODEC install (P-022, docs/DOCTOR-DESIGN.md).

    python3 tools/doctor.py            # text, one line per check, FAIL first
    python3 tools/doctor.py --json     # the same report as JSON
    python3 tools/doctor.py --repo ~/codec-repo   # compare the services with another checkout

Prints counts, modes and setting names only: never a key, a file's contents, or a file name other than
the fixed paths it checks. Writes nothing and starts or stops nothing. Exit code 1 when a check FAILs.
The checks live in codec_doctor.py (top level, so the app bundle ships them to Settings > Connectors);
this file is the command line. Stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codec_doctor  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(codec_doctor.main(sys.argv[1:]))
