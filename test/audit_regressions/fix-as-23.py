#!/usr/bin/env python3
"""Include AS-23 CLI regressions in the existing local and hosted audit gate."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] / 'lease_activity_test.py'),
              run_name='__main__')
