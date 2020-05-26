from . import qemu
from . import image
from . import ssh
from . import sshfs

import argparse
import os
import pwd
import socket

from typing import cast, Optional, List, Dict, Any, Union


class TransientVm:
    store: image.ImageStore
    config: argparse.Namespace
    vm_images: List[image.ImageInfo]
    ssh_config: Optional[ssh.SshConfig]
    qemu_runner: Optional[qemu.QemuRunner]

    def __init__(self, config: argparse.Namespace) -> None:
        self.store = image.ImageStore(backend_dir=config.image_backend,
                                      frontend_dir=config.image_frontend)
        self.config = config
        self.vm_images = []
        self.ssh_config = None
        self.qemu_runner = None

    def __create_images(self, names: List[str]) -> List[image.ImageInfo]:
        return [self.store.create_vm_image(image_name, self.config.name, idx)
                for idx, image_name in enumerate(names)]

    def __needs_ssh(self) -> bool:
        return (self.config.ssh_console is True or
                self.config.ssh_command is not None or
                self.config.ssh_with_serial is True or
                len(self.config.shared_folder) > 0)

    def __needs_ssh_console(self) -> bool:
        return (self.config.ssh_console is True or
                self.config.ssh_with_serial is True or
                self.config.ssh_command is not None)

    def __qemu_added_devices(self) -> List[str]:
        new_args = []
        for image in self.vm_images:
            new_args.extend(["-drive", "file={}".format(image.path)])

        if self.__needs_ssh():
            if self.__needs_ssh_console():
                new_args.append("-nographic")

            if self.config.ssh_port is None:
                ssh_port = self.__allocate_random_port()
            else:
                ssh_port = self.config.ssh_port

            self.ssh_config = ssh.SshConfig(host="localhost",
                                            port=ssh_port,
                                            user=self.config.ssh_user,
                                            ssh_bin_name=self.config.ssh_bin_name)

            # the random localhost port or the user provided port to guest port 22
            new_args.extend([
                "-netdev",
                "user,id=transient-sshdev,hostfwd=tcp::{}-:22".format(ssh_port),
                "-device",
                "e1000,netdev=transient-sshdev"
            ])

        return new_args

    def __allocate_random_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Binding to port 0 causes the kernel to allocate a port for us. Because
        # it won't reuse that port until is _has_ to, this can safely be used
        # as (for example) the ssh port for the guest and it 'should' be race-free
        s.bind(("", 0))
        addr = s.getsockname()
        s.close()
        return cast(int, addr[1])

    def __connect_ssh(self) -> int:
        assert(self.ssh_config is not None)
        assert(self.qemu_runner is not None)

        client = ssh.SshClient(config=self.ssh_config, command=self.config.ssh_command)
        conn = client.connect_stdout(timeout=self.config.ssh_timeout)

        # The SSH connection has been established. Silence the serial console
        self.qemu_runner.silence()

        conn.wait()
        return conn.returncode

    def __current_user(self) -> str:
        return pwd.getpwuid(os.getuid()).pw_name

    def __ssh_shutdown(self):
        client = ssh.SshClient(config=self.ssh_config, command=self.config.ssh_command)
        conn = client.connect_piped(15)
        raw_stdout, raw_stderr = conn.communicate(b'sudo shutdown -h now',
                timeout=15)
        returncode = conn.poll()
        stdout = raw_stdout.decode("utf-8").strip()
        stderr = raw_stderr.decode("utf-8").strip()
        print(stdout, stderr)


    def run(self) -> int:
        # First, download and setup any required disks
        self.vm_images = self.__create_images(self.config.image)

        if self.config.prepare_only is True:
            return 0

        print("Finished preparation. Starting virtual machine")

        added_qemu_args = self.__qemu_added_devices()
        full_qemu_args = added_qemu_args + self.config.qemu_args

        # If we are using the SSH console, we need to do _something_ with QEMU output.
        qemu_quiet, qemu_silenceable = False, False
        if self.__needs_ssh_console():
            if self.config.ssh_with_serial is True:
                qemu_quiet, qemu_silenceable = False, True
            else:
                qemu_quiet, qemu_silenceable = True, False

        self.qemu_runner = qemu.QemuRunner(full_qemu_args, quiet=qemu_quiet,
                                           silenceable=qemu_silenceable)

        self.qemu_runner.start()

        for shared_spec in self.config.shared_folder:
            assert(self.ssh_config is not None)
            local, remote = shared_spec.split(":")

            # The user almost certainly doesn't intend to pass a relative path,
            # so make it absolute
            absolute_local_path = os.path.abspath(local)
            sshfs.do_sshfs_mount(connect_timeout=self.config.ssh_timeout,
                                 ssh_config=self.ssh_config,
                                 local_dir=absolute_local_path,
                                 remote_dir=remote,
                                 local_user=self.__current_user())

        if self.__needs_ssh_console():
            returncode = self.__connect_ssh()

            # Once the ssh connection closes, terminate the VM
            self.__ssh_shutdown()
            # self.qemu_runner.terminate()
            self.qemu_runner.wait(10)

            # Note that for ssh-console, we return the code of the ssh connection,
            # not the qemu process
            return returncode
        else:
            return self.qemu_runner.wait()
