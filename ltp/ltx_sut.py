"""
.. module:: ltx
    :platform: Linux
    :synopsis: module containing LTX SUT implementation

.. moduleauthor:: Andrea Cervesato <andrea.cervesato@suse.com>
"""
import os
import re
import abc
import time
import logging
import threading
import importlib
from ltp.sut import SUT
from ltp.sut import SUTError
from ltp.sut import SUTTimeoutError
from ltp.sut import IOBuffer
from ltp.sut import TAINED_MSG
from ltp.ltx import LTX
from ltp.ltx import LTXError
from ltp.utils import Timeout


class LTXSUT(SUT):
    """
    LTX executor can be used to communicate with SUT and to execute tests,
    as well as trasfer data from target to host.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("ltp.ltx_sut")
        self._stdin = None
        self._stdout = None
        self._env = None
        self._cwd = None
        self._ltx = None
        self._stdin_fd = None
        self._stdout_fd = None
        self._table_id = []
        self._cmd_lock = threading.Lock()
        self._fetch_lock = threading.Lock()

    def setup(self, **kwargs: dict) -> None:
        if not importlib.util.find_spec('msgpack'):
            raise SUTError("'msgpack' library is not available")

        self._logger.info("Initialize LTX")

        self._stdin = kwargs.get('stdin', None)
        self._stdout = kwargs.get('stdout', None)
        self._env = kwargs.get('env', None)
        self._cwd = kwargs.get('cwd', None)

        if not self._stdin or not os.path.exists(self._stdin):
            raise LTXError("stdin must be an existing file")

        if not self._stdout or not os.path.exists(self._stdout):
            raise LTXError("stdout must be an existing file")

    @property
    def config_help(self) -> dict:
        return dict(
            stdin="executor stdin file path",
            stdout="executor stdout file path",
        )

    @property
    def name(self) -> str:
        return "ltx"

    @property
    def is_running(self) -> bool:
        return self._ltx is not None

    def _reserve_table(self) -> int:
        """
        Reserve a new table ID to execute a command.
        """
        if len(self._table_id) >= LTX.TABLE_ID_MAXSIZE:
            raise LTXError("Not enough slots for other commands")

        counter = -1

        for counter in range(0, LTX.TABLE_ID_MAXSIZE):
            if counter not in self._table_id:
                break

        self._logger.info("Reserving table ID: %d", counter)

        self._table_id.append(counter)

        return counter

    def _remove_table(self, table_id: int) -> None:
        """
        Remove a table ID from the list.
        """
        self._logger.info("Removing table ID: %d", table_id)

        self._table_id.remove(table_id)

    def _sync_execute(
            self,
            table_id: int,
            cmd: str,
            timeout: float = 3600,
            stdout_callback: callable = None) -> str:
        """
        Synchronous version of execute.
        """
        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)
            results.append(delta)
            results.append(time_ns)
            results.append(si_status)
            results.append(si_code)

        self._ltx.execute(
            table_id,
            cmd,
            stdout_callback=stdout_callback,
            callback=_callback)

        with Timeout(timeout) as timer:
            while len(results) < 5:
                time.sleep(1e-6)
                timer.check(
                    err_msg=f"Timeout during command execution: '{repr(cmd)}'",
                    exc=SUTTimeoutError)

        return results

    def _sync_cat(
            self,
            table_id: int,
            path: str) -> str:
        """
        Synchronous version of cat.
        """
        results = []

        def _callback(stdout, delta, time_ns, si_code, si_status):
            results.append(stdout)
            results.append(delta)
            results.append(time_ns)
            results.append(si_status)
            results.append(si_code)

        self._ltx.cat(table_id, [path], callback=_callback)

        with Timeout(5) as timer:
            while len(results) < 5:
                time.sleep(1e-6)
                timer.check(
                    err_msg="Timeout when reading system information",
                    exc=SUTError)

        return results

    def ping(self) -> float:
        if not self.is_running:
            raise SUTError("SUT is not running")

        time_s = 0

        with Timeout(10) as timer:
            results = []

            # pylint: disable=unnecessary-lambda
            self._ltx.ping(
                callback=lambda delta, _: results.append(delta))

            while not results:
                time.sleep(1e-6)
                timer.check(
                    err_msg="Timeout waiting for PONG",
                    exc=SUTError)

            time_s = results.pop()

        return time_s

    def get_info(self) -> dict:
        if not self.is_running:
            raise SUTError("SUT is not running")

        table_id = self._reserve_table()
        ret = None

        try:
            kernel = self._sync_execute(table_id, "uname -s -r -v")[0]
            arch = self._sync_execute(table_id, "uname -m")[0]
            cpu = self._sync_execute(table_id, "uname -p")[0]

            os_release = self._sync_cat(table_id, "/etc/os-release")[0]
            meminfo = self._sync_cat(table_id, "/proc/meminfo")[0]

            distro = re.search(r'ID="(?P<distro>.*)"\n', os_release)
            if not distro:
                raise SUTError("Can't read distro from /etc/os-release")

            distro_ver = re.search(
                r'VERSION_ID="(?P<distro_ver>.*)"\n', os_release)
            if not distro_ver:
                raise SUTError(
                    "Can't read distro version from /etc/os-release")

            swap_m = re.search(r'SwapTotal:\s+(?P<swap>\d+\s+kB)', meminfo)
            if not swap_m:
                raise SUTError(
                    "Can't read swap information from /proc/meminfo")

            mem_m = re.search(r'MemTotal:\s+(?P<memory>\d+\s+kB)', meminfo)
            if not mem_m:
                raise SUTError(
                    "Can't read memory information from /proc/meminfo")

            ret = {
                "distro": distro.group('distro'),
                "distro_ver": distro_ver.group('distro_ver'),
                "kernel": kernel.rstrip(),
                "arch": arch.rstrip(),
                "cpu": cpu.rstrip(),
                "swap": swap_m.group('swap'),
                "ram": mem_m.group('memory')
            }
        finally:
            self._remove_table(table_id)

        return ret

    def get_tained_info(self) -> set:
        if not self.is_running:
            raise SUTError("SUT is not running")

        table_id = self._reserve_table()
        code = -1
        messages = []

        try:
            stdout = self._sync_cat(table_id, "/proc/sys/kernel/tainted")[0]
            tained_num = len(TAINED_MSG)
            code = int(stdout.rstrip())
            bits = format(code, f"0{tained_num}b")[::-1]

            for i in range(0, tained_num):
                if bits[i] == "1":
                    msg = TAINED_MSG[i]
                    messages.append(msg)
        finally:
            self._remove_table(table_id)

        return code, messages

    def _stop(self, timeout: float) -> None:
        """
        Internal stop routine.
        """
        self._logger.info("Stopping LTX executions")

        results = []

        try:
            # pylint: disable=cell-var-from-loop
            for table_id in self._table_id:
                self._ltx.kill(
                    table_id,
                    callback=lambda: results.append(table_id))

            with Timeout(timeout) as timer:
                while len(results) < len(self._table_id):
                    time.sleep(1e-6)
                    timer.check(
                        err_msg="Timeout during stop",
                        exc=SUTError)

                while self._fetch_lock.locked():
                    time.sleep(1e-6)
                    timer.check(
                        err_msg="Timeout waiting for command to stop",
                        exc=SUTError)

                while self._cmd_lock.locked():
                    time.sleep(1e-6)
                    timer.check(
                        err_msg="Timeout waiting for command to stop",
                        exc=SUTError)
        except LTXError as err:
            raise SUTError(err)

        try:
            if self._stdin_fd:
                os.close(self._stdin_fd)

            if self._stdout_fd:
                os.close(self._stdout_fd)
        except OSError as err:
            raise SUTError(err)

        self._ltx = None

        self._logger.info("LTX executions stopped")

    def stop(
            self,
            timeout: float = 30,
            iobuffer: IOBuffer = None) -> None:
        self._stop(timeout)

    def force_stop(
            self,
            timeout: float = 30,
            iobuffer: IOBuffer = None) -> None:
        self._stop(timeout)

    def communicate(
            self,
            timeout: float = 3600,
            iobuffer: IOBuffer = None) -> None:
        if self.is_running:
            raise SUTError("SUT is running")

        try:
            with Timeout(timeout) as timer:
                self._logger.info("Setting up LTX")

                self._stdin_fd = os.open(self._stdin, os.O_RDWR)
                self._stdout_fd = os.open(self._stdout, os.O_RDWR)

                self._ltx = LTX()
                self._ltx.connect(self._stdin_fd, self._stdout_fd)

                timer.check(
                    err_msg="Timeout after connection",
                    exc=SUTError)

                # ping the executor to check if it's up and running
                self.ping()

                # setup environment variables
                if self._env:
                    for key, value in self._env.items():
                        results = []

                        # pylint: disable=cell-var-from-loop
                        self._ltx.env(
                            None,
                            key,
                            value,
                            callback=lambda: results.append("done"))

                        while not results:
                            time.sleep(1e-6)
                            timer.check(
                                err_msg="Timeout when setting environment",
                                exc=SUTError)

                self._logger.info("LTX service is ready")
        except LTXError as err:
            if "Timeout" in str(err):
                raise SUTTimeoutError(f"LTX: {str(err)}")

            raise SUTError(f"LTX: {str(err)}")

    def run_command(
            self,
            command: str,
            timeout: float = 3600,
            iobuffer: IOBuffer = None) -> dict:
        if not command:
            raise ValueError("command is empty")

        if not self.is_running:
            raise SUTError("SUT is not running")

        with self._cmd_lock:
            self._logger.info("Running command: %s", command)

            t_secs = max(timeout, 0)
            table_id = self._reserve_table()
            ret = None

            try:
                def _stdout_callback(data):
                    if iobuffer:
                        iobuffer.write(data)
                        iobuffer.flush()

                results = self._sync_execute(
                    table_id,
                    command,
                    timeout=t_secs,
                    stdout_callback=_stdout_callback)

                ret = {
                    "command": command,
                    "timeout": t_secs,
                    "stdout": results[0],
                    "exec_time": results[1],
                    "returncode": results[3],
                }
            except SUTTimeoutError as err:
                self._ltx.kill(table_id)
                raise err
            except LTXError as err:
                raise SUTError(f"LTX: {str(err)}")
            finally:
                self._remove_table(table_id)

            return ret

    @abc.abstractmethod
    def run_multiple_commands(
            self,
            commands: list,
            timeout: float = 3600,
            command_completed: callable = None) -> list:
        pass

    def fetch_file(
            self,
            target_path: str,
            timeout: float = 3600) -> bytes:
        if not target_path:
            raise ValueError("target path is empty")

        if not self.is_running:
            raise SUTError("SUT is not running")

        with self._fetch_lock:
            self._logger.info("Downloading %s", target_path)

            t_secs = max(timeout, 0)
            data = None

            with Timeout(t_secs) as timer:
                try:
                    results = []

                    # pylint: disable=unnecessary-lambda
                    self._ltx.get_file(
                        target_path,
                        callback=lambda data: results.append(data))

                    while not results:
                        time.sleep(1e-6)
                        timer.check(
                            err_msg="Timeout getting file",
                            exc=SUTTimeoutError)

                    data = results.pop()

                    self._logger.info("Fetching done")
                    self._logger.debug(data)
                except LTXError as err:
                    raise SUTError(f"LTX: {str(err)}")

            return data
