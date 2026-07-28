"""The console has to be readable from wherever the package was installed.

This project reported a bug against DataHub where a wheel built and installed
without complaint and was missing the data files a command read at runtime, so
the failure only appeared once someone installed it rather than cloning it.
Groundskeeper had the same defect: the server resolved console/index.html by
walking up out of the package to the repository root, which is a directory that
only exists in a checkout.

Reading through importlib.resources is what makes it correct, and a test that
goes through the same path is what keeps it correct. This does not need a wheel
to be built, so it runs everywhere the rest of the suite runs.
"""

from __future__ import annotations

from importlib import resources

from groundskeeper import server


def test_the_console_is_a_package_resource_not_a_path_out_of_the_package():
    # Anchored to the package. A path built from __file__ and parents[2] would
    # point at site-packages' parent once installed, and at nothing useful.
    package_root = resources.files("groundskeeper")
    assert str(server.CONSOLE).startswith(str(package_root))


def test_the_console_is_readable_and_is_the_real_console():
    body = server.CONSOLE.read_bytes()
    assert b"Groundskeeper" in body
    # The three surfaces the server routes to are all in this one document, so
    # a truncated or placeholder asset would not carry them.
    for marker in (b"benchmark", b"runs", b"gate"):
        assert marker in body.lower()
