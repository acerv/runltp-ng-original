"""
Unittests for ltx module.
"""
import os
import time
import signal
import subprocess
import pytest
from ltx import LTX
from ltx import LTXError
from ltp.utils import Timeout

TEST_LTX_PATH = os.environ.get("TEST_LTX_PATH", None)


@pytest.fixture
def whereis():
    """
    Wrapper around whereis command.
    """
    def _callback(binary):
        stdout = subprocess.check_output([f"whereis {binary}"], shell=True)
        paths = stdout.decode().split()[1:]

        assert len(paths) > 0
        return paths

    yield _callback


@pytest.mark.ltx
@pytest.mark.skipif(
    not TEST_LTX_PATH or not os.path.isfile(TEST_LTX_PATH),
    reason="TEST_LTX_PATH doesn't exist")
class TestLTX:
    """
    Unittest for LTX class.
    """

    @pytest.fixture
    def ltx(self):
        """
        LTX object to test.
        """
        with subprocess.Popen(
                TEST_LTX_PATH,
                bufsize=0,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE) as proc:

            ltx = LTX()
            ltx.connect(
                proc.stdin.fileno(),
                proc.stdout.fileno())

            yield ltx

            ltx.disconnect()

    def test_version(self, ltx):
        """
        Test version method.
        """
        results = []

        ltx.version(callback=lambda version: results.append(version))

        with Timeout(2) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        version = results.pop()
        assert version is not None

    def test_ping(self, ltx):
        """
        Test ping method.
        """
        results = []
        start_t = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 10e8

        def _callback(delta, time_ns):
            results.append(delta)
            results.append(time_ns)

        ltx.ping(callback=_callback)

        with Timeout(2) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        end_t = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 10e8

        time_ns = results.pop()
        assert start_t < time_ns < end_t

    @pytest.mark.skip()
    def test_ping_flood(self, ltx):
        """
        Test ping method when called multiple times.
        """
        results = []
        start_t = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 10e8

        for _ in range(2048):
            ltx.ping(callback=lambda delta, time_ns: results.append(time_ns))

        with Timeout(10) as timer:
            while len(results) < 2048:
                time.sleep(1e-6)
                timer.check()

        end_t = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) * 10e8

        for time_ns in results:
            assert start_t < time_ns < end_t

    def test_exec(self, ltx, whereis):
        """
        Test exec method.
        """
        paths = whereis("uname")

        results = []
        stdout = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)
            results.append(delta)
            results.append(time_ns)
            results.append(si_status)
            results.append(si_code)

        def _stdout_callback(data):
            stdout.append(data)

        ltx.execute(
            0,
            paths[0],
            stdout_callback=_stdout_callback,
            callback=_callback)

        with Timeout(2) as timer:
            while len(results) < 5:
                time.sleep(1e-6)
                timer.check()

        assert results[0] is not None
        assert results[1] > 0
        assert results[2] > 0
        assert results[3] == 0
        assert results[4] == 1
        assert stdout.pop() == "Linux\n"

    @pytest.mark.skip()
    def test_exec_multiple(self, ltx):
        """
        Test multiple commands by calling exec multiple times.
        """
        results = []

        def _callback(stdout, time_ns, si_code, si_status):
            results.append(stdout)

        for table_id in range(ltx.TABLE_ID_MAXSIZE):
            ltx.execute(
                table_id,
                "echo ciao",
                callback=_callback)

        with Timeout(2) as timer:
            while len(results) < ltx.TABLE_ID_MAXSIZE:
                time.sleep(1e-6)
                timer.check()

        for result in results:
            assert result == "ciao\n"

    def test_exec_builtin(self, ltx):
        """
        Test exec method with builtin commands.
        """
        results = []
        stdout = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)
            results.append(delta)
            results.append(time_ns)
            results.append(si_status)
            results.append(si_code)

        def _stdout_callback(data):
            stdout.append(data)

        ltx.execute(
            0,
            "echo ciao",
            stdout_callback=_stdout_callback,
            callback=_callback)

        with Timeout(2) as timer:
            while len(results) < 5:
                time.sleep(1e-6)
                timer.check()

        assert results[0] is not None
        assert results[1] > 0
        assert results[2] > 0
        assert results[3] == 0
        assert results[4] == 1
        assert stdout.pop() == "ciao\n"

    def test_set_file(self, ltx, tmp_path):
        """
        Test set_file method.
        """
        data = b'AaXa\x00\x01\x02Zz' * 2048
        pfile = tmp_path / 'file.bin'
        results = []

        ltx.set_file(str(pfile), data, callback=lambda: results.append("done"))

        with Timeout(5) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        content = pfile.read_bytes()
        assert content == data

    def test_get_file(self, ltx, tmp_path):
        """
        Test get_file method.
        """
        pattern = b'AaXa\x00\x01\x02Zz' * 2048
        pfile = tmp_path / 'file.bin'
        pfile.write_bytes(pattern)
        results = []

        ltx.get_file(str(pfile), callback=lambda data: results.append(data))

        with Timeout(5) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        data = results.pop()
        assert pattern == data

    def test_kill(self, ltx, whereis):
        """
        Test kill method.
        """
        paths = whereis("sleep")
        table_id = 0
        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)
            results.append(delta)
            results.append(time_ns)
            results.append(si_status)
            results.append(si_code)

        ltx.execute(table_id, f"{paths[0]} 5", callback=_callback)
        time.sleep(0.1)
        ltx.kill(table_id)

        with Timeout(5) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        assert results[0] == ""
        assert results[1] < 1
        assert results[2] > 0
        assert results[3] == signal.SIGKILL
        assert results[4] == 2

    def test_env(self, ltx, whereis):
        """
        Test setting env variables
        """
        path = whereis("printenv")
        setup = []

        ltx.env(
            0,
            "LTPROOT",
            "/opt/ltp",
            callback=lambda: setup.append("done"))

        with Timeout(2) as timer:
            while not setup:
                time.sleep(1e-6)
                timer.check()

        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)

        ltx.execute(0, f"{path[0]} LTPROOT", callback=_callback)

        with Timeout(2) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        stdout = results.pop()
        assert stdout == "/opt/ltp\n"

    @pytest.mark.skip()
    def test_env_multiple(self, ltx, whereis):
        """
        Test env variables when set for all commands.
        """
        path = whereis("printenv")
        setup = []

        ltx.env(
            None,
            "LTPROOT",
            "/opt/ltp",
            callback=lambda: setup.append("done"))

        with Timeout(2) as timer:
            while not setup:
                time.sleep(1e-6)
                timer.check()

        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)

        for table_id in range(ltx.TABLE_ID_MAXSIZE):
            ltx.execute(table_id, f"{path[0]} LTPROOT", callback=_callback)

        with Timeout(2) as timer:
            while len(results) < ltx.TABLE_ID_MAXSIZE:
                time.sleep(1e-6)
                timer.check()

        for stdout in results:
            assert stdout == "/opt/ltp\n"

    def test_cat(self, ltx):
        """
        Test the cat method.
        """
        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)

        ltx.cat(
            0,
            ["/proc/sys/kernel/tainted"],
            callback=_callback)

        with Timeout(2) as timer:
            while not results:
                time.sleep(1e-6)
                timer.check()

        stdout = results.pop()
        assert int(stdout.rstrip()) >= 0
