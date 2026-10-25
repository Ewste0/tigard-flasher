"""Browser-test server with descriptor fixtures; never enumerate physical USB."""
from unittest.mock import patch

from spc5tool.cli import main
from tests.backends import tigard_descriptor


if __name__ == "__main__":
    with patch("spc5tool.web.discover_tigards", return_value=[tigard_descriptor("TEST-ONLY")]):
        raise SystemExit(main())
