"""Only GUI entrypoint. No backend startup, services, or worker lifecycle calls."""
import sys
from src.gui.app import main

if __name__=='__main__':
    raise SystemExit(main())
