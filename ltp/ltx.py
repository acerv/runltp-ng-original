"""
.. module:: ltx
    :platform: Linux
    :synopsis: module containing LTX SUT implementation

.. moduleauthor:: Andrea Cervesato <andrea.cervesato@suse.com>
"""
import os
import logging
import threading
from ltp.sut import SUT
from ltp.sut import SUTError
from ltp.sut import SUTTimeoutError
from ltp.sut import IOBuffer
from ltx.ltx import LTX
from ltx.ltx import LTXError


class LTXSUT(SUT):
    """
    LTX executor can be used to communicate with SUT and to execute tests,
    as well as trasfer data from target to host.
    """

    def __init__(self, **kwargs: dict) -> None:
        """
        :param env: environment variables
        :type env: dict
        :param cwd: current working directory
        :type cwd: str
        :param tmpdir: temporary directory
        :type tmpdir: str
        :param stdin: LTX stdin file path
        :type stdin: str
        :param stdout: LTX stdout file path
        :type stdout: str
        """
        self._stdin = kwargs.get('stdin', None)
        self._stdout = kwargs.get('stdout', None)

        if not self._stdin:
            raise ValueError("stdin is empty")

        if not self._stdout:
            raise ValueError("stdout is empty")

        self._env = kwargs.get('env', None)
        self._cwd = kwargs.get('cwd', None)
        self._ltx = None
        self._stdin_fd = None
        self._stdout_fd = None
        self._table_id = None
        self._cmd_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._logger = logging.getLogger("ltp.ltx")

    @property
    def name(self) -> str:
        return "ltx"

    @property
    def is_running(self) -> bool:
        return self._ltx is not None

    def ping(self) -> float:
        if not self.is_running:
            raise SUTError("SUT is not running")

        time_ns = self._ltx.ping()
        time_s = time_ns / 10e9

        return time_s

    def get_info(self) -> dict:
        # TODO
        return {
            "distro": "linux",
            "distro_ver": "unknown",
            "kernel": "unknown",
            "arch": "unknown"
        }

    def get_tained_info(self) -> set:
        return 0, []

    def _stop(self) -> None:
        """
        Internal stop routine.
        """
        self._logger.info("Stopping LTX executions")

        try:
            if self._table_id:
                self._ltx.kill(self._table_id)
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
        self._stop()

    def force_stop(
        self,
        timeout: float = 30,
        iobuffer: IOBuffer = None) -> None:
        self._stop()

    def communicate(
        self,
        timeout: float = 3600,
        iobuffer: IOBuffer = None) -> None:
        try:
            self._logger.info("Setting up LTX")

            self._stdin_fd = os.open(self._stdin, os.O_RDWR)
            self._stdout_fd = os.open(self._stdout, os.O_RDWR)

            self._ltx = LTX(self._stdin_fd, self._stdout_fd)

            self._ltx.ping(timeout=timeout)

            for key, value in self._env.items():
                self._ltx.env(None, key, value, timeout=timeout)

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
            self._logger.info(f"Running command '{command}'")

            t_secs = max(timeout, 0)

            try:
                self._table_id = self._ltx.reserve()

                def _callback(data):
                    if not iobuffer:
                        return

                    iobuffer.write(data.encode(encoding="utf-8"))
                    iobuffer.flush()

                stdout, \
                time_ns, \
                _, \
                si_status = self._ltx.execute(
                    self._table_id,
                    command,
                    timeout=t_secs,
                    stdout_callback=_callback)

                ret = {
                    "command": command,
                    "stdout": stdout,
                    "returncode": si_status,
                    "timeout": t_secs,
                    "exec_time": time_ns / 10e9,
                }

                return ret
            except LTXError as err:
                if "Timeout" in str(err):
                    raise SUTTimeoutError(f"LTX: {str(err)}")

                raise SUTError(f"LTX: {str(err)}")

    def fetch_file(
            self,
            target_path: str,
            timeout: float = 3600) -> bytes:
        if not target_path:
            raise ValueError("target path is empty")

        with self._fetch_lock:
            self._logger.info(f"Downloading {target_path}")

            t_secs = max(timeout, 0)
            try:
                data = self._ltx.get_file(target_path, timeout=t_secs)

                self._logger.info(f"Fetching done")
                return data
            except LTXError as err:
                if "Timeout" in str(err):
                    raise SUTTimeoutError(f"LTX: {str(err)}")

                raise SUTError(f"LTX: {str(err)}")
