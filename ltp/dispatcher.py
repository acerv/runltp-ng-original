"""
.. module:: dispatcher
    :platform: Linux
    :synopsis: module containing Dispatcher definition and implementation.

.. moduleauthor:: Andrea Cervesato <andrea.cervesato@suse.com>
"""
import os
import re
import sys
import time
import logging
import threading
import ltp
import ltp.data
from ltp import LTPException
from ltp.sut import SUT
from ltp.sut import IOBuffer
from ltp.sut import SUTTimeoutError
from ltp.sut import KernelPanicError
from ltp.data import Test
from ltp.data import Suite
from ltp.results import TestResults
from ltp.results import SuiteResults
from ltp.utils import Timeout


class DispatcherError(LTPException):
    """
    Raised when a error occurs during dispatcher operations.
    """


class SuiteTimeoutError(LTPException):
    """
    Raised when suite reaches timeout during execution.
    """


class Dispatcher:
    """
    A dispatcher that schedule jobs to run on target.
    """

    @property
    def is_running(self) -> bool:
        """
        Returns True if dispatcher is running tests. False otherwise.
        """
        raise NotImplementedError()

    @property
    def last_results(self) -> list:
        """
        Last testing suites results.
        :returns: list(SuiteResults)
        """
        raise NotImplementedError()

    def stop(self, timeout: float = 30) -> None:
        """
        Stop the current execution.
        :param timeout: timeout before stopping dispatcher
        :type timeout: float
        """
        raise NotImplementedError()

    def exec_suites(self, suites: list, skip_tests: list = None) -> list:
        """
        Execute a list of testing suites.
        :param suites: list of Suite objects
        :type suites: list(str)
        :param skip_tests: list of tests to skip
        :type skip_tests: list(str)
        :returns: list(SuiteResults)
        """
        raise NotImplementedError()


class StdoutChecker(IOBuffer):
    """
    Check for test's stdout and raise an exception if Kernel panic occured.
    """

    def __init__(self, test: Test) -> None:
        self.stdout = ""
        self._test = test
        self._line = ""

    def write(self, data: str) -> None:
        if len(data) == 1:
            self._line += data
            if data == "\n":
                ltp.events.fire(
                    "test_stdout_line",
                    self._test,
                    self._line[:-1])
                self._line = ""
        else:
            lines = data.split('\n')
            for line in lines[:-1]:
                self._line += line
                ltp.events.fire("test_stdout_line", self._test, self._line)
                self._line = ""

            self._line = lines[-1]

            if data.endswith('\n') and self._line:
                ltp.events.fire("test_stdout_line", self._test, self._line)
                self._line = ""

        self.stdout += data

    def flush(self) -> None:
        pass


class RedirectStdout(IOBuffer):
    """
    Redirect data from stdout to events.
    """

    def __init__(self, sut: SUT) -> None:
        self._sut = sut

    def write(self, data: str) -> None:
        if not self._sut:
            return

        ltp.events.fire("sut_stdout_line", self._sut.name, data)

    def flush(self) -> None:
        pass


class BaseDispatcher(Dispatcher):
    """
    Basic class for dispatcher implementation.
    """

    def __init__(self, **kwargs: dict) -> None:
        self._logger = logging.getLogger("ltp.dispatcher")
        self._ltpdir = kwargs.get("ltpdir", None)
        self._tmpdir = kwargs.get("tmpdir", None)
        self._sut = kwargs.get("sut", None)
        self._suite_timeout = max(kwargs.get("suite_timeout", 3600.0), 0.0)
        self._test_timeout = max(kwargs.get("test_timeout", 3600.0), 0.0)
        self._exec_lock = threading.Lock()
        self._stop = False
        self._last_results = None

        if not self._ltpdir:
            raise ValueError("LTP directory doesn't exist")

        if not self._sut:
            raise ValueError("SUT object is empty")

        # create temporary directory where saving suites files
        self._tmpdir.mkdir("runtest")

    def exec_suites(self, suites: list, skip_tests: list = None) -> list:
        raise NotImplementedError()

    @property
    def is_running(self) -> bool:
        # some pylint versions don't recognize threading.Lock::locked
        # pylint: disable=no-member
        return self._exec_lock.locked()

    @property
    def last_results(self) -> list:
        return self._last_results

    def stop(self, timeout: float = 30) -> None:
        if not self.is_running:
            return

        self._logger.info("Stopping dispatcher")

        self._stop = True

        try:
            with Timeout(timeout) as timer:
                while self.is_running:
                    time.sleep(0.05)
                    timer.check(
                        err_msg="Timeout when stopping dispatcher",
                        exc=DispatcherError)
        finally:
            self._stop = False

        self._logger.info("Dispatcher stopped")

    @staticmethod
    def _command_from_test(test: Test) -> str:
        """
        Returns a command from test.
        """
        cmd = test.command
        if len(test.arguments) > 0:
            cmd += ' '
            cmd += ' '.join(test.arguments)

        return cmd

    @staticmethod
    def _get_test_results(
            test: Test,
            test_data: dict,
            timed_out: bool = False) -> TestResults:
        """
        Return test results accoding with runner output and Test definition.
        :param test: Test definition object
        :type test: Test
        :param test_data: output data from a runner execution
        :type test_data: dict
        :param timed_out: if True, test will be considered broken by default
        :type timed_out: bool
        :returns: TestResults
        """
        stdout = test_data["stdout"]

        # get rid of colors from stdout
        stdout = re.sub(r'\u001b\[[0-9;]+[a-zA-Z]', '', stdout)

        match = re.search(
            r"Summary:\n"
            r"passed\s*(?P<passed>\d+)\n"
            r"failed\s*(?P<failed>\d+)\n"
            r"broken\s*(?P<broken>\d+)\n"
            r"skipped\s*(?P<skipped>\d+)\n"
            r"warnings\s*(?P<warnings>\d+)\n",
            stdout
        )

        passed = 0
        failed = 0
        skipped = 0
        broken = 0
        skipped = 0
        warnings = 0
        retcode = test_data["returncode"]
        exec_time = test_data["exec_time"]

        if match:
            passed = int(match.group("passed"))
            failed = int(match.group("failed"))
            skipped = int(match.group("skipped"))
            broken = int(match.group("broken"))
            skipped = int(match.group("skipped"))
            warnings = int(match.group("warnings"))
        else:
            passed = stdout.count("TPASS")
            failed = stdout.count("TFAIL")
            skipped = stdout.count("TSKIP")
            broken = stdout.count("TBROK")
            warnings = stdout.count("TWARN")

            if passed == 0 and \
                    failed == 0 and \
                    skipped == 0 and \
                    broken == 0 and \
                    warnings == 0:
                # if no results are given, this is probably an
                # old test implementation that fails when return
                # code is != 0
                if retcode == 0:
                    passed = 1
                elif retcode == 4:
                    warnings = 1
                elif retcode == 32:
                    skipped = 1
                else:
                    failed = 1

        if timed_out:
            broken = 1

        result = TestResults(
            test=test,
            failed=failed,
            passed=passed,
            broken=broken,
            skipped=skipped,
            warnings=warnings,
            exec_time=exec_time,
            retcode=retcode,
            stdout=stdout,
        )

        return result

    def _download_suites(self, suites: list) -> list:
        """
        Download all testing suites and return suites objects.
        """
        suites_obj = []

        for suite_name in suites:
            target = os.path.join(self._ltpdir, "runtest", suite_name)

            ltp.events.fire(
                "suite_download_started",
                suite_name,
                target)

            data = self._sut.fetch_file(target)
            data_str = data.decode(encoding="utf-8", errors="ignore")

            self._tmpdir.mkfile(os.path.join("runtest", suite_name), data_str)

            ltp.events.fire(
                "suite_download_completed",
                suite_name,
                target)

            suite = ltp.data.read_runtest(suite_name, data_str)
            suites_obj.append(suite)

        return suites_obj

    def _reboot_sut(self, force: bool = False) -> None:
        """
        This method reboot SUT if needed, for example, after a Kernel panic.
        """
        self._logger.info("Rebooting SUT")
        ltp.events.fire("sut_restart", self._sut.name)

        if force:
            self._sut.force_stop(timeout=360)
        else:
            self._sut.stop(timeout=360)

        self._sut.communicate(
            timeout=3600,
            iobuffer=RedirectStdout(self._sut))

        self._logger.info("SUT rebooted")

    def _write_kmsg(self, test: Test) -> None:
        """
        If root, we write test information on /dev/kmsg.
        """
        self._logger.info("Writing test information on /dev/kmsg")

        if os.geteuid() != 0:
            self._logger.info("Can't write on /dev/kmsg from user")
            return

        with open('/dev/kmsg', 'w', encoding='utf-8') as kmsg:
            cmd = self._command_from_test(test)

            kmsg.write(
                f'{sys.argv[0]}[{os.getpid()}]: '
                f'starting test {test.name} ({cmd})\n')


class SerialDispatcher(BaseDispatcher):
    """
    Dispatcher implementation that serially runs test suites one after
    the other.
    """

    def _run_test(self, test: Test) -> TestResults:
        """
        Execute a test and return the results.
        """
        self._logger.info("Running test %s", test.name)
        self._logger.debug(test)

        ltp.events.fire("test_started", test)

        self._write_kmsg(test)

        cmd = self._command_from_test(test)

        test_data = None

        # check for tained kernel status
        tained_code_before, tained_msg_before = self._sut.get_tained_info()
        if tained_msg_before:
            for msg in tained_msg_before:
                ltp.events.fire("kernel_tained", msg)
                self._logger.debug("Kernel tained before test: %s", msg)

        timed_out = False
        reboot = False

        checker = StdoutChecker(test)
        try:
            test_data = self._sut.run_command(
                cmd,
                timeout=self._test_timeout,
                iobuffer=checker)
        except SUTTimeoutError:
            timed_out = True
            try:
                self._sut.ping()

                # SUT replies -> test timed out
                ltp.events.fire(
                    "test_timed_out",
                    test.name,
                    self._test_timeout)
            except SUTTimeoutError:
                reboot = True
                ltp.events.fire("sut_not_responding")
        except KernelPanicError:
            timed_out = True
            reboot = True
            ltp.events.fire("kernel_panic")
            self._logger.debug("Kernel panic recognized")

        if not reboot:
            # check again for tained kernel and if tained status has changed
            # just raise an exception and reboot the SUT
            tained_code_after, tained_msg_after = self._sut.get_tained_info()
            if tained_code_before != tained_code_after:
                reboot = True
                for msg in tained_msg_after:
                    ltp.events.fire("kernel_tained", msg)
                    self._logger.debug("Kernel tained after test: %s", msg)

        if timed_out:
            test_data = {
                "name": test.name,
                "command": test.command,
                "stdout": checker.stdout,
                "returncode": -1,
                "exec_time": self._test_timeout,
            }

        results = self._get_test_results(
            test,
            test_data,
            timed_out=timed_out)

        ltp.events.fire("test_completed", results)

        self._logger.info("Test completed")
        self._logger.debug(results)

        if reboot:
            # reboot the system if it's not host
            if self._sut.name != "host":
                self._reboot_sut(force=True)

        return results

    def _run_suite(
            self,
            suite: Suite,
            info: dict,
            skip_tests: str = None) -> None:
        """
        Execute a specific testing suite and return the results.
        """
        self._logger.info("Running suite %s", suite.name)
        self._logger.debug(suite)

        # execute suite tests
        ltp.events.fire("suite_started", suite)

        start_t = time.time()
        tests_results = []
        timed_out = False
        interrupt = False

        for test in suite.tests:
            if self._stop:
                break

            if timed_out or interrupt:
                # after suite timeout treat all tests left as skipped tests
                result = TestResults(
                    test=test,
                    failed=0,
                    passed=0,
                    broken=0,
                    skipped=1,
                    warnings=0,
                    exec_time=0.0,
                    retcode=32,
                    stdout="",
                )
                tests_results.append(result)
                continue

            if skip_tests and re.search(skip_tests, test.name):
                self._logger.info("Ignoring test: %s", test.name)
                continue

            try:
                results = self._run_test(test)
                if results:
                    tests_results.append(results)
            except KeyboardInterrupt:
                # catch SIGINT during test execution and postpone it after
                # results have been collected, so we don't loose tests reports
                interrupt = True

            if time.time() - start_t >= self._suite_timeout:
                timed_out = True

        if not tests_results:
            # no tests execution means no suite
            return

        exec_time = 0.0
        for result in tests_results:
            exec_time += result.exec_time

        suite_results = SuiteResults(
            suite=suite,
            tests=tests_results,
            distro=info["distro"],
            distro_ver=info["distro_ver"],
            kernel=info["kernel"],
            arch=info["arch"],
            cpu=info["cpu"],
            swap=info["swap"],
            ram=info["ram"],
            exec_time=exec_time)

        self._last_results.append(suite_results)

        if suite_results:
            ltp.events.fire("suite_completed", suite_results)

        self._logger.debug(suite_results)
        self._logger.info("Suite completed")

        if interrupt:
            raise KeyboardInterrupt()

        if timed_out:
            self._logger.info("Testing suite timed out: %s", suite.name)

            ltp.events.fire(
                "suite_timeout",
                suite,
                self._suite_timeout)

            raise SuiteTimeoutError(
                f"{suite.name} suite timed out "
                f"(timeout={self._suite_timeout})")

    def exec_suites(self, suites: list, skip_tests: str = None) -> list:
        if not suites:
            raise ValueError("Empty suites list")

        with self._exec_lock:
            self._last_results = []

            suites_obj = self._download_suites(suites)
            info = self._sut.get_info()

            for suite in suites_obj:
                self._run_suite(suite, info, skip_tests=skip_tests)

            return self._last_results


class ParallelDispatcher(BaseDispatcher):
    """
    Execute testing suites by running tests in parallel.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._cpu_count = os.cpu_count()
        self._logger.info("Available CPUs: %d", self._cpu_count)
        self._keyboard_interrupt = False
        self._batch_results = None
        self._batch_tests = None

    def _save_result(self, data: set) -> None:
        """
        Each test that will complete, it will run this method
        and we will populate test results.
        """
        res = data[0]
        exc = data[1]

        self._logger.info("Received result: %s, %s", res, repr(exc))

        mytest = None
        for test in self._batch_tests:
            cmd = self._command_from_test(test)
            if cmd == res["command"]:
                mytest = test
                break

        if exc:
            self._logger.info(
                "Catched exception for test %s: %s",
                mytest.name,
                exc)

        test_results = self._get_test_results(
            mytest,
            res,
            timed_out=isinstance(exc, SUTTimeoutError))

        ltp.events.fire("test_completed", test_results)

        self._batch_results.append(test_results)

    def _run_tests(self, tests: list) -> None:
        """
        Execute a set of tests and return the list of results.
        """
        self._batch_results = []
        self._batch_tests = tests

        self._logger.info(
            "Running tests: %s",
            ' '.join(test.name for test in tests))

        for test in tests:
            ltp.events.fire("test_started", test)

        cmds = [self._command_from_test(test) for test in tests]

        try:
            self._sut.run_multiple_commands(
                cmds,
                timeout=self._test_timeout,
                command_completed=self._save_result)
        except KeyboardInterrupt:
            # since we are working with parallel execution, we need
            # to catch keyboard interrupt and to handle it when tests
            # results are being collected
            self._keyboard_interrupt = True

    def _run_suite(
            self,
            suite: Suite,
            info: dict,
            skip_tests: str = None) -> None:
        """
        Execute a specific testing suite and return the results.
        """
        self._logger.info("Running suite %s", suite.name)
        self._logger.debug(suite)

        ltp.events.fire("suite_started", suite)

        start_t = time.time()
        tests_results = []
        timed_out = False
        tests_set = []

        # create set of tests based on number of CPUs
        if not skip_tests:
            for i in range(0, len(suite.tests), self._cpu_count):
                tests_set.append(suite.tests[i:i + self._cpu_count])
        else:
            for i in range(0, len(suite.tests), self._cpu_count):
                tests = []

                for j in range(i, i + self._cpu_count):
                    if j >= len(suite.tests):
                        break

                    test_name = suite.tests[j].name

                    if re.search(skip_tests, test_name):
                        self._logger.info("Ignoring test: %s", test_name)
                        continue

                    tests.append(suite.tests[j])

                if tests:
                    tests_set.append(tests)

        # execute sets of tests
        t_start = time.time()

        for tests in tests_set:
            if self._stop or self._keyboard_interrupt:
                break

            self._run_tests(tests)

            tests_results.extend(self._batch_results)

            if time.time() - start_t >= self._suite_timeout:
                timed_out = True
                break

        exec_time = time.time() - t_start

        if not tests_results:
            # no tests execution means no suite
            return

        suite_results = SuiteResults(
            suite=suite,
            tests=tests_results,
            distro=info["distro"],
            distro_ver=info["distro_ver"],
            kernel=info["kernel"],
            arch=info["arch"],
            cpu=info["cpu"],
            swap=info["swap"],
            ram=info["ram"],
            exec_time=exec_time)

        self._last_results.append(suite_results)

        if suite_results:
            ltp.events.fire("suite_completed", suite_results)

        self._logger.debug(suite_results)
        self._logger.info("Suite completed")

        if self._keyboard_interrupt:
            raise KeyboardInterrupt()

        if timed_out:
            self._logger.info("Testing suite timed out: %s", suite.name)

            ltp.events.fire(
                "suite_timeout",
                suite,
                self._suite_timeout)

            raise SuiteTimeoutError(
                f"{suite.name} suite timed out "
                f"(timeout={self._suite_timeout})")

    def exec_suites(self, suites: list, skip_tests: str = None) -> list:
        if not suites:
            raise ValueError("Empty suites list")

        with self._exec_lock:
            self._last_results = []
            self._batch_tests = []
            self._batch_results = []
            self._keyboard_interrupt = False

            suites_obj = self._download_suites(suites)
            info = self._sut.get_info()

            for suite in suites_obj:
                self._run_suite(suite, info, skip_tests=skip_tests)

            return self._last_results
