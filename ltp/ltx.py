"""
.. module:: ltx
    :platform: Linux
    :synopsis: module containing LTX communication class

.. moduleauthor:: Andrea Cervesato <andrea.cervesato@suse.com>
"""
import os
import re
import time
import select
import logging
import threading
from ltp import LTPException

try:
    import msgpack
except ModuleNotFoundError:
    pass


class LTXError(LTPException):
    """
    Raised when an error occurs during LTX execution.
    """


class Request:
    """
    LTX request.
    """
    PING = 0
    PONG = 1
    ENV = 2
    EXEC = 3
    LOG = 4
    RESULT = 5
    GET_FILE = 6
    SET_FILE = 7
    DATA = 8
    KILL = 9
    VERSION = 10
    CAT = 11

    def __init__(self, **kwargs: dict) -> None:
        """
        :param stdin: stdin file descriptor
        :type stdin: int
        :param callback: callback called when request complete. Can be None
        :type callback: callable
        :param args: request arguments
        :type args: list
        """
        self._logger = logging.getLogger("ltx.request")
        self._stdin_fd = kwargs.get("stdin", None)
        self._callback = kwargs.get("callback", None)
        self._args = kwargs.get("args", [])
        self._completed = False
        self._request_id = -1
        self._sent = False

        if not self._stdin_fd:
            raise ValueError("stdin is empty")

    def feed(self, message: list) -> None:
        """
        Feed request queue with data and set result when the request
        has completed.
        :param message: processed msgpack message
        :type message: list
        """
        raise NotImplementedError()

    @property
    def sent(self) -> dict:
        """
        True when request has been sent.
        """
        return self._sent

    @property
    def completed(self) -> dict:
        """
        True when request has been completed.
        """
        return self._completed

    def send(self) -> None:
        """
        Send a message to stdin.
        """
        msg = []
        msg.append(self._request_id)
        msg.extend(self._args)

        self._logger.info("Sending message: %s", msg)

        data = msgpack.packb(msg)

        try:
            bytes_towrite = len(data)
            bytes_written = os.write(self._stdin_fd, data)

            if bytes_written != bytes_towrite:
                raise LTXError(
                    f"Can't send all {bytes_towrite} bytes. "
                    f" Only {bytes_written} bytes were written.")

            self._sent = True
        except BrokenPipeError:
            pass


class PingRequest(Request):
    """
    PING request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._echoed = False
        self._request_id = self.PING
        self._start_t = 0

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[0] == self.PING:
            self._logger.info("PING echoed back")
            self._logger.info("Waiting for PONG")
            self._start_t = time.time()
            self._echoed = True
        elif message[0] == self.PONG:
            if not self._echoed:
                raise LTXError("PONG received without PING echo")

            delta = time.time() - self._start_t
            end_t = message[1]

            self._logger.debug(
                "delta=%s, end_t=%s",
                delta,
                end_t)

            if self._callback:
                self._callback(delta, end_t)

            self._completed = True


class EnvRequest(Request):
    """
    ENV request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._table_id = self._args[0]
        self._request_id = self.ENV

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[1] != self._table_id:
            return

        if message[0] == self.ENV:
            self._logger.info("ENV echoed back")

            if self._callback:
                self._callback()

            self._completed = True


class ExecRequest(Request):
    """
    EXEC request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._stdout_callback = kwargs.get("stdout_callback", None)
        self._table_id = self._args[0]
        self._stdout = []
        self._echoed = False
        self._request_id = self.EXEC
        self._start_t = 0

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[1] != self._table_id:
            return

        if message[0] == self.EXEC:
            self._logger.info("EXEC echoed back")
            self._start_t = time.time()
            self._echoed = True
        elif message[0] == self.LOG:
            if not self._echoed:
                raise LTXError("LOG received without EXEC echo")

            log = message[3]

            self._logger.info("LOG replied with data: %s", repr(log))
            self._stdout.append(log)

            if self._stdout_callback:
                self._stdout_callback(log)
        elif message[0] == self.RESULT:
            if not self._echoed:
                raise LTXError("RESULT received without EXEC echo")

            self._logger.info("RESULT received")

            stdout = "".join(self._stdout)
            delta = time.time() - self._start_t
            time_ns = message[2]
            si_code = message[3]
            si_status = message[4]

            self._logger.debug(
                "delta=%s, time_ns=%s, si_code=%s, si_status=%s",
                delta,
                time_ns,
                si_code,
                si_status)

            if self._callback:
                self._callback(stdout, delta, time_ns, si_code, si_status)

            self._completed = True


class KillRequest(Request):
    """
    KILL request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._table_id = self._args[0]
        self._request_id = self.KILL

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[1] != self._table_id:
            return

        if message[0] == self.KILL:
            self._logger.info("KILL echoed back")

            if self._callback:
                self._callback()

            self._completed = True


class GetFileRequest(Request):
    """
    GET_FILE request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._echoed = False
        self._request_id = self.GET_FILE

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[0] == self.GET_FILE:
            self._logger.info("GET_FILE echoed back")
            self._echoed = True
        elif message[0] == self.DATA:
            if not self._echoed:
                raise LTXError("DATA received without GET_FILE echo")

            self._logger.info("Data received")
            self._logger.debug("data=%s", message[1])

            if self._callback:
                self._callback(message[1])

            self._completed = True


class SetFileRequest(Request):
    """
    SET_FILE request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._request_id = self.SET_FILE

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[0] == self.SET_FILE and message[1] == self._args[0]:
            self._logger.info("SETFILE echoed back")

            if self._callback:
                self._callback()

            self._completed = True


class VersionRequest(Request):
    """
    VERSION request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._echoed = False
        self._request_id = self.VERSION

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[0] == self._request_id:
            self._logger.info("VERSION echoed back")
            self._echoed = True
        elif message[0] == self.LOG and message[1] is None:
            if not self._echoed:
                raise LTXError("LOG received without VERSION echo")

            match = re.match(r'LTX Version=(?P<version>.*)', message[3])
            if match:
                version = match.group("version").rstrip()

                if self._callback:
                    self._callback(version)

                self._completed = True


class CatRequest(Request):
    """
    CAT request.
    """

    def __init__(self, **kwargs: dict) -> None:
        super().__init__(**kwargs)

        self._table_id = self._args[0]
        self._stdout = []
        self._echoed = False
        self._request_id = self.CAT
        self._start_t = 0

    def feed(self, message: list) -> None:
        if not self.sent or self.completed:
            return

        if message[1] != self._table_id:
            return

        if message[0] == self.CAT:
            self._logger.info("CAT echoed back")
            self._start_t = time.time()
            self._echoed = True
        elif message[0] == self.LOG:
            if not self._echoed:
                raise LTXError("LOG received without CAT echo")

            log = message[3]

            self._logger.info("LOG replied with data: %s", repr(log))
            self._stdout.append(log)
        elif message[0] == self.RESULT:
            if not self._echoed:
                raise LTXError("RESULT received without CAT echo")

            self._logger.info("RESULT received")

            stdout = "".join(self._stdout)
            delta = time.time() - self._start_t
            time_ns = message[2]
            si_code = message[3]
            si_status = message[4]

            self._logger.debug(
                "delta=%s, time_ns=%s, si_code=%s, si_status=%s",
                delta,
                time_ns,
                si_code,
                si_status)

            if self._callback:
                self._callback(stdout, delta, time_ns, si_code, si_status)

            self._completed = True


class LTX:
    """
    LTX executor is a simple and fast service built in C, used to send commands
    on SUT, as well as fetching or sending files to it. This class has been
    created to communicate with LTX using python.
    """
    BUFFSIZE = 1 << 21
    TABLE_ID_MAXSIZE = 128

    def __init__(self) -> None:
        self._logger = logging.getLogger("ltx")
        self._requests = []
        self._stop_reading = False
        self._connected = False
        self._stdin_fd = None
        self._stdout_fd = None
        self._producer_thread = None

    def _feed_requests(self, msg: list) -> None:
        """
        Feed requests with the given message.
        """
        completed = []

        for request in self._requests:
            request.feed(msg)

            if request.completed:
                completed.append(request)

        for request in completed:
            self._requests.remove(request)

    def _producer(self) -> None:
        """
        Read messages coming from the service.
        """
        self._logger.info("Starting message polling")

        poller = select.epoll()
        poller.register(self._stdout_fd, select.EPOLLIN)

        # force utf-8 encoding by using raw=False
        unpacker = msgpack.Unpacker(raw=False)

        while not self._stop_reading:
            events = poller.poll(0.1)

            for fdesc, _ in events:
                if fdesc != self._stdout_fd:
                    continue

                data = os.read(self._stdout_fd, self.BUFFSIZE)
                if not data:
                    continue

                unpacker.feed(data)

                self._logger.debug("Unpacking bytes: %s", data)

                while True:
                    try:
                        message = unpacker.unpack()
                        if message:
                            self._logger.info("Received message: %s", message)
                            self._feed_requests(message)
                    except msgpack.OutOfData:
                        break

        self._logger.info("Ending message polling")

    def _send_request(self, request: Request) -> None:
        """
        Send a request and wait for reply.
        """
        self._requests.append(request)
        request.send()

    def _check_connection(self) -> None:
        """
        Check if client is connected to the service. If not, it raises
        an exception.
        """
        if not self._connected:
            raise LTXError("Client is not connected to LTX")

    def _check_table_id(self, table_id: int) -> None:
        """
        Check if `table_id` is in between bounds and eventually rise an
        exception.
        """
        if table_id < 0 or table_id >= self.TABLE_ID_MAXSIZE:
            raise ValueError("Out of bounds table ID [0-127]")

    def connect(self, stdin_fd: int, stdout_fd: int) -> None:
        """
        Connect to the LTX service via file descriptors.
        :param stdin_fd: stdin file descriptor
        :type stdin_fd: int
        :param stdout_fd: stdout file descriptor
        :type stdout_fd: int
        """
        if self._connected:
            return

        if not stdin_fd or stdin_fd < 0:
            raise ValueError("Invalid stdin file descriptor")

        if not stdout_fd or stdout_fd < 0:
            raise ValueError("Invalid stdout file descriptor")

        self._stop_reading = False
        self._stdin_fd = stdin_fd
        self._stdout_fd = stdout_fd

        self._producer_thread = threading.Thread(
            target=self._producer,
            daemon=True)
        self._producer_thread.start()

        self._connected = True

    def disconnect(self) -> None:
        """
        Disconnect from LTX service.
        """
        self._stop_reading = True
        self._producer_thread.join(timeout=30)
        self._connected = False

    def version(self, callback: callable) -> None:
        """
        Get executor version.
        :param callback: called when version is received
        :type callback: callable
        """
        self._check_connection()

        self._logger.info("Asking for version")

        request = VersionRequest(
            stdin=self._stdin_fd,
            callback=callback)

        self._send_request(request)

    def ping(self, callback: callable) -> None:
        """
        Ping executor and wait for pong.
        :param callback: called when pong is received
        :type callback: callable
        """
        self._check_connection()

        self._logger.info("Sending ping")

        request = PingRequest(
            stdin=self._stdin_fd,
            callback=callback)

        self._send_request(request)

    def env(self,
            table_id: int,
            key: str,
            value: str,
            callback: callable) -> None:
        """
        Set environment variable.
        :param table_id: command table ID. Can be None if we want to apply the
            same environment variables to all commands
        :type table_id: int
        :param key: key of the environment variable
        :type key: str
        :param value: value of the environment variable
        :type value: str
        :param callback: called when environment variable is set
        :type callback: callable
        """
        if not key:
            raise ValueError("key is empty")

        if not value:
            raise ValueError("value is empty")

        if table_id:
            self._check_table_id(table_id)

        self._check_connection()

        self._logger.info("Setting env: %s=%s", key, value)

        request = EnvRequest(
            stdin=self._stdin_fd,
            callback=callback,
            args=[table_id, key, value])

        self._send_request(request)

    def get_file(self, path: str, callback: callable) -> None:
        """
        Read a file and return its content.
        :param path: path of the file
        :type path: str
        :param callback: called when file is received
        :type callback: callable
        """
        if not path:
            raise ValueError("path is empty")

        self._check_connection()

        self._logger.info("Getting file: %s", path)

        request = GetFileRequest(
            stdin=self._stdin_fd,
            callback=callback,
            args=[path])

        self._send_request(request)

    def set_file(self, path: str, data: bytes, callback: callable) -> None:
        """
        Send a file.
        :param path: path of the file to write
        :type path: str
        :param data: data to write on file
        :type data: bytes
        :param callback: called when file is stored
        :type callback: callable
        """
        if not path:
            raise ValueError("path is empty")

        if not data:
            raise ValueError("data is empty")

        self._check_connection()

        self._logger.info("Setting file: %s", path)

        request = SetFileRequest(
            stdin=self._stdin_fd,
            callback=callback,
            args=[path, data])

        self._send_request(request)

    def execute(self,
                table_id: int,
                command: str,
                stdout_callback: callable = None,
                callback: callable = None) -> None:
        """
        Execute a command.
        :param table_id: command table ID
        :type table_id: int
        :param command: command to run
        :type command: str
        :param stdout_callback: called when new data arrives inside stdout
        :type stdout_callback: callable
        :param callback: called when command has executed
        :type callback: callable
        """
        if not command:
            raise ValueError("Command is empty")

        self._check_table_id(table_id)
        self._check_connection()

        args = [table_id]
        args.extend(command.split())

        request = ExecRequest(
            stdin=self._stdin_fd,
            stdout_callback=stdout_callback,
            callback=callback,
            args=args)

        self._send_request(request)

    def kill(self, table_id: int, callback: callable = None) -> None:
        """
        Kill a command.
        :param table_id: command table ID
        :type table_id: int
        :param callback: called when command has been killed
        :type callback: callable
        """
        self._check_table_id(table_id)

        self._logger.info("Killing process on table ID: %d", table_id)

        request = KillRequest(
            stdin=self._stdin_fd,
            callback=callback,
            args=[table_id])

        self._send_request(request)

    def cat(self,
            table_id: int,
            files: list,
            callback: callable = None) -> None:
        """
        Cat a list of files.
        :param table_id: command table ID
        :type table_id: int
        :param files: list of files to cat
        :type files: list(str)
        :param callback: called when cat finished to read files
        :type callback: callable
        """
        if not files:
            raise ValueError("files list is empty")

        for path in files:
            if not path:
                raise ValueError("files list contain an empty element")

        self._check_table_id(table_id)

        self._logger.info("Cat files")
        self._logger.debug(files)

        args = [table_id]
        args.extend(files)

        request = CatRequest(
            stdin=self._stdin_fd,
            callback=callback,
            args=args)

        self._send_request(request)
