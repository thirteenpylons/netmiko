import os
import re
import socket
import time

from netmiko.cisco_base_connection import CiscoSSHConnection
from netmiko.cisco_base_connection import CiscoFileTransfer
from netmiko.ssh_exception import NetmikoTimeoutException

LINUX_PROMPT_PRI = os.getenv("NETMIKO_LINUX_PROMPT_PRI", "$")
LINUX_PROMPT_ALT = os.getenv("NETMIKO_LINUX_PROMPT_ALT", "#")
LINUX_PROMPT_ROOT = os.getenv("NETMIKO_LINUX_PROMPT_ROOT", "#")


class LinuxSSH(CiscoSSHConnection):
    def session_preparation(self):
        """Prepare the session after the connection has been established."""
        self.ansi_escape_codes = True
        return super().session_preparation()

    def _enter_shell(self):
        """Already in shell."""
        return ""

    def _return_cli(self):
        """The shell is the CLI."""
        return ""

    def disable_paging(self, *args, **kwargs):
        """Linux doesn't have paging by default."""
        return ""

    def set_base_prompt(
        self,
        pri_prompt_terminator=LINUX_PROMPT_PRI,
        alt_prompt_terminator=LINUX_PROMPT_ALT,
        delay_factor=1,
    ):
        """Determine base prompt."""
        return super().set_base_prompt(
            pri_prompt_terminator=pri_prompt_terminator,
            alt_prompt_terminator=alt_prompt_terminator,
            delay_factor=delay_factor,
        )

    def send_config_set(self, config_commands=None, exit_config_mode=True, **kwargs):
        """Can't exit from root (if root)"""
        if self.username == "root":
            exit_config_mode = False
        return super().send_config_set(
            config_commands=config_commands, exit_config_mode=exit_config_mode, **kwargs
        )

    def check_config_mode(self, check_string=LINUX_PROMPT_ROOT, pattern=""):
        """Verify root"""
        return self.check_enable_mode(check_string=check_string)

    def config_mode(
        self,
        config_command: str = "sudo -s",
        pattern: str = "ssword",
        re_flags: int = re.IGNORECASE,
    ) -> str:
        """Attempt to become root."""
        return self.enable(cmd=config_command, pattern=pattern, re_flags=re_flags)

    def exit_config_mode(self, exit_config="exit"):
        return self.exit_enable_mode(exit_command=exit_config)

    def check_enable_mode(self, check_string=LINUX_PROMPT_ROOT):
        """Verify root"""
        return super().check_enable_mode(check_string=check_string)

    def exit_enable_mode(self, exit_command="exit"):
        """Exit enable mode."""
        delay_factor = self.select_delay_factor(delay_factor=0)
        output = ""
        if self.check_enable_mode():
            self.write_channel(self.normalize_cmd(exit_command))
            time.sleep(0.3 * delay_factor)
            self.set_base_prompt()
            if self.check_enable_mode():
                raise ValueError("Failed to exit enable mode.")
        return output

    def enable(self, cmd="sudo -s", pattern="ssword", re_flags=re.IGNORECASE):
        """Attempt to become root."""
        delay_factor = self.select_delay_factor(delay_factor=0)
        output = ""
        if not self.check_enable_mode():
            self.write_channel(self.normalize_cmd(cmd))
            time.sleep(0.3 * delay_factor)
            try:
                output += self.read_channel()
                if re.search(pattern, output, flags=re_flags):
                    self.write_channel(self.normalize_cmd(self.secret))
                self.set_base_prompt()
            except socket.timeout:
                raise NetmikoTimeoutException(
                    "Timed-out reading channel, data not available."
                )
            if not self.check_enable_mode():
                msg = (
                    "Failed to enter enable mode. Please ensure you pass "
                    "the 'secret' argument to ConnectHandler."
                )
                raise ValueError(msg)
        return output

    def cleanup(self, command="exit"):
        """Try to Gracefully exit the SSH session."""
        return super().cleanup(command=command)

    def save_config(self, *args, **kwargs):
        """Not Implemented"""
        raise NotImplementedError


class LinuxFileTransfer(CiscoFileTransfer):
    """
    Linux SCP File Transfer driver.

    Mostly for testing purposes.
    """

    def __init__(
        self,
        ssh_conn,
        source_file,
        dest_file,
        file_system="/var/tmp",
        direction="put",
        **kwargs,
    ):
        return super().__init__(
            ssh_conn=ssh_conn,
            source_file=source_file,
            dest_file=dest_file,
            file_system=file_system,
            direction=direction,
            **kwargs,
        )

    def remote_space_available(self, search_pattern=""):
        """Return space available on remote device."""
        return self._remote_space_available_unix(search_pattern=search_pattern)

    def check_file_exists(self, remote_cmd=""):
        """Check if the dest_file already exists on the file system (return boolean)."""
        return self._check_file_exists_unix(remote_cmd=remote_cmd)

    def remote_file_size(self, remote_cmd="", remote_file=None):
        """Get the file size of the remote file."""
        return self._remote_file_size_unix(
            remote_cmd=remote_cmd, remote_file=remote_file
        )

    def remote_md5(self, base_cmd="md5sum", remote_file=None):
        if remote_file is None:
            if self.direction == "put":
                remote_file = self.dest_file
            elif self.direction == "get":
                remote_file = self.source_file
        remote_md5_cmd = f"{base_cmd} {self.file_system}/{remote_file}"
        dest_md5 = self.ssh_ctl_chan.send_command(remote_md5_cmd, read_timeout=300)
        dest_md5 = self.process_md5(dest_md5)
        return dest_md5

    @staticmethod
    def process_md5(md5_output, pattern=r"^(\S+)\s+"):
        return super(LinuxFileTransfer, LinuxFileTransfer).process_md5(
            md5_output, pattern=pattern
        )

    def enable_scp(self, cmd=None):
        raise NotImplementedError

    def disable_scp(self, cmd=None):
        raise NotImplementedError
