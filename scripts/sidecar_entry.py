"""PyInstaller freezes a script, not `-m muse`."""
from muse.__main__ import main

raise SystemExit(main())
