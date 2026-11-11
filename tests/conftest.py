import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _no_owner_tools_recorded():
    """Each test starts as a process that has registered no owner tools.

    Preflight refuses a home whose process registered the owner tools (the ``owner_tools`` check). That record
    is per process. Many suites register the plugin with a fake host and then drive the co-host in the same
    process, the way a stage process does, so one test's registration must not decide another test's
    preflight. The tests of the check itself register after this has run.
    """
    from onair import preflight

    preflight._OWNER_TOOLSETS.clear()
    yield
