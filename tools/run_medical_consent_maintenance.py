#!/usr/bin/env python3
"""Repository entry point for the packaged medical consent maintenance worker."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Backend/Flask'))
from medical_consent_maintenance import backfill, main
if __name__=='__main__': raise SystemExit(main())
