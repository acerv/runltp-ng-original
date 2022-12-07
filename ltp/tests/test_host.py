"""
Test SUT implementations.
"""
import time
import signal
import threading
import pytest
from ltp.sut import SUTTimeoutError
from ltp.host import HostSUT
from ltp.tests.sut import _TestSUT
from ltp.tests.sut import Printer


@pytest.fixture
def sut():
    sut = HostSUT()
    sut.setup()

    yield sut

    if sut.is_running:
        sut.force_stop()


class TestHostSUT(_TestSUT):
    """
    Test HostSUT implementation.
    """

    @pytest.fixture
    def sut_stop_sleep(self, request):
        """
        Host SUT test doesn't require time sleep in `test_stop_communicate`.
        """
        return request.param * 0

    def test_cwd(self, tmpdir):
        """
        Test CWD constructor argument.
        """
        myfile = tmpdir / "myfile"
        myfile.write("runltp-ng tests")

        sut = HostSUT()
        sut.setup(cwd=str(tmpdir))
        sut.communicate(iobuffer=Printer())

        ret = sut.run_command("cat myfile", timeout=2, iobuffer=Printer())
        assert ret["returncode"] == 0
        assert ret["stdout"] == "runltp-ng tests"

    def test_env(self, tmpdir):
        """
        Test ENV constructor argument.
        """
        myfile = tmpdir / "myfile"
        myfile.write("runltp-ng tests")

        sut = HostSUT()
        sut.setup(cwd=str(tmpdir), env=dict(FILE=str(myfile)))
        sut.communicate(iobuffer=Printer())

        ret = sut.run_command("cat $FILE", timeout=2, iobuffer=Printer())
        assert ret["returncode"] == 0
        assert ret["stdout"] == "runltp-ng tests"

    def test_run_multiple_commands_timeout(self, sut):
        """
        Test run_multiple_commands method on timeout.
        """
        sut.communicate(iobuffer=Printer())
        commands = []

        def command_complete(data: set) -> None:
            assert data[0]["command"] is not None
            assert data[0]["timeout"] == 0.1
            assert data[0]["returncode"] == -signal.SIGTERM
            assert data[0]["stdout"] == ""
            assert 0 < data[0]["exec_time"] < 1
            assert isinstance(data[1], SUTTimeoutError)

        for index in range(0, 10):
            commands.append(f"sleep 10; echo -n {index}")

        t_start = time.time()
        results = sut.run_multiple_commands(
            commands,
            timeout=0.1,
            command_completed=command_complete)
        t_end = time.time() - t_start

        assert t_end <= 0.5

        for index in range(0, 10):
            result, exc = results[index]

            assert result["command"] == f"sleep 10; echo -n {index}"
            assert result["timeout"] == 0.1
            assert result["returncode"] == -signal.SIGTERM
            assert result["stdout"] == ""
            assert 0 < result["exec_time"] < 1
            assert isinstance(exc, SUTTimeoutError)

    def test_run_multiple_commands(self, sut):
        """
        Test run_multiple_commands method.
        """
        sut.communicate(iobuffer=Printer())
        commands = []

        def command_complete(data: set) -> None:
            assert data[0]["command"] is not None
            assert data[0]["timeout"] == 0.5
            assert data[0]["returncode"] == 0
            assert data[0]["stdout"] is not None
            assert 0 < data[0]["exec_time"] < time.time()
            assert data[1] is None

        for index in range(0, 10):
            commands.append(f"sleep 0.1; echo -n {index}")

        t_start = time.time()
        results = sut.run_multiple_commands(
            commands,
            timeout=0.5,
            command_completed=command_complete)
        t_end = time.time() - t_start

        assert t_end <= 0.5

        for index in range(0, 10):
            result, exc = results[index]

            assert exc is None
            assert result["command"] == f"sleep 0.1; echo -n {index}"
            assert result["timeout"] == 0.5
            assert result["returncode"] == 0
            assert result["stdout"] == f"{index}"
            assert 0 < result["exec_time"] < time.time()

    @pytest.mark.parametrize("force", [False, True])
    def test_stop_run_multiple_commands(self, sut, force):
        """
        Test run_multiple_commands method when stopped.
        """
        sut.communicate(iobuffer=Printer())
        commands = []

        def command_complete(data: set) -> None:
            assert data[0]["command"] is not None
            assert data[0]["timeout"] == 12
            assert data[0]["stdout"] is not None
            assert 0 < data[0]["exec_time"] < time.time()
            assert data[1] is None

            if force:
                assert data[0]["returncode"] == -signal.SIGKILL
            else:
                assert data[0]["returncode"] == -signal.SIGHUP

        for index in range(0, 10):
            commands.append(f"sleep 10; echo -n {index}")

        def _stop():
            time.sleep(0.5)
            if force:
                sut.force_stop()
            else:
                sut.stop()

        thread = threading.Thread(target=_stop, daemon=True)
        thread.start()

        t_start = time.time()
        results = sut.run_multiple_commands(
            commands,
            timeout=12,
            command_completed=command_complete)
        t_end = time.time() - t_start

        thread.join(timeout=1)

        assert t_end <= 1

        for index in range(0, 10):
            result, exc = results[index]

            assert exc is None
            assert result["command"] == f"sleep 10; echo -n {index}"
            assert result["timeout"] == 12
            assert result["stdout"] == ""
            assert 0 < result["exec_time"] < 1

            if force:
                assert result["returncode"] == -signal.SIGKILL
            else:
                assert result["returncode"] == -signal.SIGHUP
