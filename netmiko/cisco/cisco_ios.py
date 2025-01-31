import time
import re
import os
import hashlib
import io

from netmiko.cisco_base_connection import CiscoBaseConnection, CiscoFileTransfer


class CiscoIosBase(CiscoBaseConnection):
    """Common Methods for IOS (both SSH and telnet)."""

    def __init__(self, *args, **kwargs):
        # Cisco-IOS defaults to fast_cli=True and legacy_mode=False
        kwargs.setdefault("fast_cli", True)
        kwargs.setdefault("_legacy_mode", False)
        return super().__init__(*args, **kwargs)

    def session_preparation(self):
        """Prepare the session after the connection has been established."""
        cmd = "terminal width 511"
        self.set_terminal_width(command=cmd, pattern=cmd)
        self.disable_paging()
        self.set_base_prompt()

    def check_config_mode(self, check_string=")#", pattern="#"):
        """
        Checks if the device is in configuration mode or not.

        Cisco IOS devices abbreviate the prompt at 20 chars in config mode
        """
        return super().check_config_mode(check_string=check_string, pattern=pattern)

    def save_config(self, cmd="write mem", confirm=False, confirm_response=""):
        """Saves Config Using Copy Run Start"""
        return super().save_config(
            cmd=cmd, confirm=confirm, confirm_response=confirm_response
        )


class CiscoIosSSH(CiscoIosBase):
    """Cisco IOS SSH driver."""

    pass


class CiscoIosTelnet(CiscoIosBase):
    """Cisco IOS Telnet driver."""

    pass


class CiscoIosSerial(CiscoIosBase):
    """Cisco IOS Serial driver."""

    pass


class CiscoIosFileTransfer(CiscoFileTransfer):
    """Cisco IOS SCP File Transfer driver."""

    pass


class InLineTransfer(CiscoIosFileTransfer):
    """Use TCL on Cisco IOS to directly transfer file."""

    def __init__(
        self,
        ssh_conn,
        source_file=None,
        dest_file=None,
        file_system=None,
        direction="put",
        source_config=None,
        socket_timeout=10.0,
        progress=None,
        progress4=None,
    ):

        if source_file and source_config:
            msg = "Invalid call to InLineTransfer both source_file and source_config specified."
            raise ValueError(msg)
        if direction != "put":
            raise ValueError("Only put operation supported by InLineTransfer.")

        if progress is not None or progress4 is not None:
            raise NotImplementedError(
                "Progress bar is not supported on inline transfers."
            )
        else:
            self.progress = progress
            self.progress4 = progress4

        self.ssh_ctl_chan = ssh_conn
        if source_file:
            self.source_file = source_file
            self.source_config = None
            self.source_md5 = self.file_md5(source_file)
            self.file_size = os.stat(source_file).st_size
        elif source_config:
            self.source_file = None
            self.source_config = source_config
            self.source_md5 = self.config_md5(source_config)
            self.file_size = len(source_config.encode("UTF-8"))
        self.dest_file = dest_file
        self.direction = direction

        if not file_system:
            self.file_system = self.ssh_ctl_chan._autodetect_fs()
        else:
            self.file_system = file_system

        self.socket_timeout = socket_timeout

    @staticmethod
    def _read_file(file_name):
        with io.open(file_name, "rt", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _tcl_newline_rationalize(tcl_string):
        r"""
        When using put inside a TCL {} section the newline is considered a new TCL
        statement and causes a missing curly-brace message. Convert "\n" to "\r". TCL
        will convert the "\r" to a "\n" i.e. you will see a "\n" inside the file on the
        Cisco IOS device.
        """
        NEWLINE = r"\n"
        CARRIAGE_RETURN = r"\r"
        tmp_string = re.sub(NEWLINE, CARRIAGE_RETURN, tcl_string)
        if re.search(r"[{}]", tmp_string):
            msg = "Curly brace detected in string; TCL requires this be escaped."
            raise ValueError(msg)
        return tmp_string

    def __enter__(self):
        self._enter_tcl_mode()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        _ = self._exit_tcl_mode()  # noqa

    def _enter_tcl_mode(self):
        TCL_ENTER = "tclsh"
        cmd_failed = ['Translating "tclsh"', "% Unknown command", "% Bad IP address"]
        output = self.ssh_ctl_chan.send_command(
            TCL_ENTER,
            expect_string=r"\(tcl\)#",
            strip_prompt=False,
            strip_command=False,
        )
        for pattern in cmd_failed:
            if pattern in output:
                raise ValueError(f"Failed to enter tclsh mode on router: {output}")
        return output

    def _exit_tcl_mode(self):
        TCL_EXIT = "tclquit"
        self.ssh_ctl_chan.write_channel("\r")
        time.sleep(1)
        output = self.ssh_ctl_chan.read_channel()
        if "(tcl)" in output:
            self.ssh_ctl_chan.write_channel(TCL_EXIT + "\r")
        time.sleep(1)
        output += self.ssh_ctl_chan.read_channel()
        return output

    def establish_scp_conn(self):
        raise NotImplementedError

    def close_scp_chan(self):
        raise NotImplementedError

    def local_space_available(self):
        raise NotImplementedError

    def file_md5(self, file_name):
        """Compute MD5 hash of file."""
        file_contents = self._read_file(file_name)
        file_contents = file_contents + "\n"  # Cisco IOS automatically adds this
        file_contents = file_contents.encode("UTF-8")
        return hashlib.md5(file_contents).hexdigest()

    def config_md5(self, source_config):
        """Compute MD5 hash of text."""
        file_contents = source_config + "\n"  # Cisco IOS automatically adds this
        file_contents = file_contents.encode("UTF-8")
        return hashlib.md5(file_contents).hexdigest()

    def put_file(self):
        curlybrace = r"{"
        TCL_FILECMD_ENTER = 'puts [open "{}{}" w+] {}'.format(
            self.file_system, self.dest_file, curlybrace
        )
        TCL_FILECMD_EXIT = "}"

        if self.source_file:
            file_contents = self._read_file(self.source_file)
        elif self.source_config:
            file_contents = self.source_config
        file_contents = self._tcl_newline_rationalize(file_contents)

        # Try to remove any existing data
        self.ssh_ctl_chan.clear_buffer()

        self.ssh_ctl_chan.write_channel(TCL_FILECMD_ENTER)
        time.sleep(0.25)
        self.ssh_ctl_chan.write_channel(file_contents)
        self.ssh_ctl_chan.write_channel(TCL_FILECMD_EXIT + "\r")

        # This operation can be slow (depends on the size of the file)
        read_timeout = 100
        sleep_time = 4
        if self.file_size >= 2500:
            read_timeout = 300
            sleep_time = 12
        elif self.file_size >= 7500:
            read_timeout = 600
            sleep_time = 25

        # Initial delay
        time.sleep(sleep_time)

        # File paste and TCL_FILECMD_exit should be indicated by "router(tcl)#"
        output = self.ssh_ctl_chan.read_until_pattern(
            pattern=r"\(tcl\).*$", re_flags=re.M, read_timeout=read_timeout
        )

        # The file doesn't write until tclquit
        TCL_EXIT = "tclquit"
        self.ssh_ctl_chan.write_channel(TCL_EXIT + "\r")

        time.sleep(1)
        # Read all data remaining from the TCLSH session
        pattern = rf"tclquit.*{self.ssh_ctl_chan.base_prompt}.*$"
        re_flags = re.DOTALL | re.M
        output += self.ssh_ctl_chan.read_until_pattern(
            pattern=pattern, re_flags=re_flags, read_timeout=read_timeout
        )
        return output

    def get_file(self):
        raise NotImplementedError

    def enable_scp(self, cmd=None):
        raise NotImplementedError

    def disable_scp(self, cmd=None):
        raise NotImplementedError
