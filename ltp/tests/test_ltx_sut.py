"""
Test LTX SUT implementations.
"""
import os
import subprocess
from ltp.ltx_sut import LTXSUT
from ltp.tests.sut import _TestSUT
import pytest

TEST_LTX_PATH = os.environ.get("TEST_LTX_PATH", None)


@pytest.mark.ltx
@pytest.mark.skipif(
    not TEST_LTX_PATH or not os.path.isfile(TEST_LTX_PATH),
    reason="TEST_LTX_PATH doesn't exist")
class TestLTXSUT(_TestSUT):
    """
    Test LTXSUT implementation.
    """

    @pytest.fixture
    def sut(self, tmp_path):
        """
        LTXSUT instance object.
        """
        stdin_path = tmp_path / 'transport.in'
        stdout_path = tmp_path / 'transport.out'

        os.mkfifo(str(stdin_path))
        os.mkfifo(str(stdout_path))

        stdin = os.open(stdin_path, os.O_RDONLY | os.O_NONBLOCK)
        stdout = os.open(stdout_path, os.O_RDWR)

        proc = subprocess.Popen(
            TEST_LTX_PATH,
            stdin=stdin,
            stdout=stdout,
            stderr=stdout,
            shell=True)

        sut = LTXSUT()
        sut.setup(
            stdin=stdin_path,
            stdout=stdout_path)

        yield sut

        if sut.is_running:
            sut.force_stop(timeout=1)

        proc.kill()

    @pytest.mark.skip(reason="Not implemented by LTX")
    def test_stop_fetch_file(self, sut, force):
        pass
