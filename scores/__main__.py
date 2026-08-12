"""Entry point so `python -m scores --selftest` works like the other modules'
`python classify.py --selftest`. The CLI itself lives in __init__.main()."""

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
