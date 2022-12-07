"""
.. module:: host
    :platform: Linux
    :synopsis: module containing host SUT implementation

.. moduleauthor:: Andrea Cervesato <andrea.cervesato@suse.com>
"""
import os
import time
import select
import signal
import logging
import threading
import subprocess
from ltp.sut import SUT
from ltp.sut import IOBuffer
from ltp.sut import SUTError
from ltp.sut import SUTTimeoutError
from ltp.utils import Timeout


class Command:
    """
    Structure containing command process data.
    It is used during parallel execution.
    """

    def __init__(
            self,
            cmd: str,
            proc: subprocess.Popen,
            timeout: float) -> None:
        self.cmd = cmd
        self.proc = proc
        self.stdout = ""
        self.timeout = timeout
        self.t_start = time.time()
        self.t_end = 0

    def to_result(self) -> dict:
        """
        Translate table into command result.
        """
        ret = {
            "command": self.cmd,
            "stdout": self.stdout,
            "returncode": self.proc.returncode,
            "timeout": self.timeout,
            "exec_time": self.t_end,
        }

        return ret


class HostSUT(SUT):
    """
    SUT implementation using host's shell.
    """

    # hack: this parameter is useful during unit testing, since we can
    # override it without using PropertyMock that seems to be bugged
    NAME = "host"

    # process stdout read buffer size
    BUFFSIZE = 1 << 20

    def __init__(self) -> None:
        self._logger = logging.getLogger("ltp.host")
        self._initialized = False
        self._cmd_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._mprocs = []
        self._stop = False
        self._cwd = None
        self._env = None

    def setup(self, **kwargs: dict) -> None:
        self._logger.info("Initialize SUT")

        self._cwd = kwargs.get('cwd', None)
        self._env = kwargs.get('env', None)

    @property
    def config_help(self) -> dict:
        # cwd and env are given by default, so no options are needed
        return {}

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def is_running(self) -> bool:
        return self._initialized

    def ping(self) -> float:
        if not self.is_running:
            raise SUTError("SUT is not running")

        ret = self.run_command("test .", timeout=1)
        reply_t = ret["exec_time"]

        return reply_t

    def communicate(self,
                    timeout: float = 3600,
                    iobuffer: IOBuffer = None) -> None:
        if self.is_running:
            raise SUTError("SUT is running")

        self._initialized = True

    def _kill_procs(self, sig: int) -> None:
        """
        Kill processes with a specific signal.
        """
        self._logger.info("Terminating processes with %s", sig)

        for proc in self._mprocs:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), sig)

    # some pylint versions don't recognize threading.Lock.locked()
    # pylint: disable=no-member
    def _inner_stop(self, sig: int, timeout: float = 30) -> None:
        """
        Wait process to stop.
        """
        if not self.is_running:
            return

        self._stop = True

        if self._mprocs:
            self._kill_procs(sig)
            self._mprocs.clear()

        with Timeout(timeout) as timer:
            while self._fetch_lock.locked():
                time.sleep(1e-6)
                timer.check(err_msg="Timeout waiting for command to stop")

            while self._cmd_lock.locked():
                time.sleep(1e-6)
                timer.check(err_msg="Timeout waiting for command to stop")

        self._logger.info("Process terminated")

        self._initialized = False

    def stop(
            self,
            timeout: float = 30,
            iobuffer: IOBuffer = None) -> None:
        self._inner_stop(signal.SIGHUP, timeout)

    def force_stop(
            self,
            timeout: float = 30,
            iobuffer: IOBuffer = None) -> None:
        self._inner_stop(signal.SIGKILL, timeout)

    def _read_stdout(self, stdout_fd: int, iobuffer: IOBuffer = None) -> str:
        """
        Read data from stdout.
        """
        if not self.is_running:
            return None

        data = os.read(stdout_fd, self.BUFFSIZE)
        rdata = data.decode(encoding="utf-8", errors="replace")
        rdata = rdata.replace('\r', '')

        # write on stdout buffers
        if iobuffer:
            iobuffer.write(rdata)
            iobuffer.flush()

        return rdata

    # pylint: disable=too-many-locals
    def run_command(self,
                    command: str,
                    timeout: float = 3600,
                    iobuffer: IOBuffer = None) -> dict:
        if not command:
            raise ValueError("command is empty")

        if not self.is_running:
            raise SUTError("SUT is not running")

        with self._cmd_lock:
            t_secs = max(timeout, 0)
            poller = select.epoll()
            ret = None

            self._stop = False

            self._logger.info(
                "Executing command (timeout=%d): %s",
                t_secs,
                repr(command))

            # pylint: disable=consider-using-with
            # pylint: disable=subprocess-popen-preexec-fn
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self._cwd,
                env=self._env,
                shell=True,
                preexec_fn=os.setsid)

            self._mprocs.append(proc)

            t_start = time.time()
            t_end = 0
            stdout = ""
            stdout_fd = proc.stdout.fileno()
            retcode = 0

            try:
                poller.register(
                    stdout_fd,
                    select.POLLIN |
                    select.POLLPRI)

                with Timeout(timeout) as timer:
                    while True:
                        events = poller.poll(0.1)
                        for fdesc, _ in events:
                            if fdesc != stdout_fd:
                                break

                            data = self._read_stdout(stdout_fd, iobuffer)
                            if data:
                                stdout += data

                        if proc.poll() is not None:
                            break

                        timer.check(
                            err_msg="Timeout during command execution",
                            exc=SUTTimeoutError)

                t_end = time.time() - t_start

                # once the process stopped, we still might have some data
                # inside the stdout buffer
                while not self._stop:
                    data = self._read_stdout(stdout_fd, iobuffer)
                    if not data:
                        break

                    stdout += data

                retcode = proc.returncode
            except SUTTimeoutError as err:
                proc.kill()
                raise err
            finally:
                ret = {
                    "command": command,
                    "stdout": stdout,
                    "returncode": retcode,
                    "timeout": t_secs,
                    "exec_time": t_end,
                }

                self._logger.debug("return data=%s", ret)

            self._logger.info("Command executed")

            return ret

    # pylint: disable=too-many-locals
    # pylint: disable=too-many-statements
    def run_multiple_commands(
            self,
            commands: list,
            timeout: float = 3600.0,
            command_completed: callable = None) -> list:
        if not commands:
            raise ValueError("command is empty")

        if not self.is_running:
            raise SUTError("SUT is not running")

        with self._cmd_lock:
            poller = select.epoll()
            t_secs = max(timeout, 0)
            t_start = time.time()
            timed_out = False
            execs = {}

            self._stop = False

            for command in commands:
                self._logger.info(
                    "Executing command (timeout=%d): %s",
                    t_secs,
                    repr(command))

                # pylint: disable=consider-using-with
                # pylint: disable=subprocess-popen-preexec-fn
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self._cwd,
                    env=self._env,
                    shell=True,
                    preexec_fn=os.setsid)

                self._mprocs.append(proc)

                stdout_fd = proc.stdout.fileno()

                execs[stdout_fd] = Command(
                    command,
                    proc,
                    t_secs)

                poller.register(
                    stdout_fd,
                    select.POLLIN |
                    select.POLLPRI)

            self._logger.info("Reading commands stdout")

            while True:
                events = poller.poll(0.1)
                for fdesc, _ in events:
                    if fdesc not in execs:
                        break

                    cmd_desc = execs[fdesc]

                    data = self._read_stdout(fdesc)
                    if data:
                        cmd_desc.stdout += data

                completed = 0
                for fdesc, cmd_desc in execs.items():
                    if cmd_desc.t_end == 0 and \
                            cmd_desc.proc.poll() is not None:
                        cmd_desc.t_end = time.time() - cmd_desc.t_start

                        # read data left in stdout if process is not stopped
                        if not self._stop:
                            data = self._read_stdout(fdesc)
                            if data:
                                cmd_desc.stdout += data

                        if command_completed:
                            exc = None
                            if timed_out:
                                exc = SUTTimeoutError(
                                    "Timeout on commands execution")

                            command_completed((cmd_desc.to_result(), exc))

                    if cmd_desc.t_end > 0:
                        completed += 1

                if completed == len(execs):
                    # completed all processes
                    break

                if time.time() - t_start >= t_secs:
                    self._logger.info("Timeout on commands execution")

                    timed_out = True

                    # kill all processes and let polling goes on to read stdout
                    # left. Polling will stop when all processes will have
                    # proc.poll() != None
                    self._kill_procs(signal.SIGTERM)

            results = []
            for _, cmd_desc in execs.items():
                exc = None
                if timed_out and cmd_desc.t_end >= t_secs:
                    exc = SUTTimeoutError("Timeout on commands execution")

                ret = cmd_desc.to_result()
                results.append((ret, exc))

            self._logger.info("Command executed")

            return results

    def fetch_file(
            self,
            target_path: str,
            timeout: float = 3600) -> bytes:
        if not target_path:
            raise ValueError("target path is empty")

        if not os.path.isfile(target_path):
            raise SUTError(f"'{target_path}' file doesn't exist")

        with self._fetch_lock:
            self._logger.info("Downloading '%s'", target_path)
            self._stop = False

            retdata = bytes()

            try:
                with Timeout(timeout) as timer:
                    with open(target_path, 'rb') as ftarget:
                        data = ftarget.read(1024)

                        while data != b'' and not self._stop:
                            retdata += data
                            data = ftarget.read(1024)

                            timer.check(
                                err_msg=f"Timeout when transfer {target_path}"
                                f" (timeout={timeout})",
                                exc=SUTTimeoutError)
            except IOError as err:
                raise SUTError(err)
            finally:
                if self._stop:
                    self._logger.info("Copy stopped")
                else:
                    self._logger.info("File copied")

            return retdata
