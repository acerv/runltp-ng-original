qemu-system-aarch64 -serial chardev:serial0\
                    -machine virt,usb=off,gic-version=3,its=off \
                    -device virtio-serial -chardev pipe,id=virtio_console,path=virtio_console,logfile=virtio_console.log,logappend=on \
                    -device virtconsole,chardev=virtio_console,name=org.openqa.console.virtio_console \
                    -chardev pipe,id=virtio_console1,path=virtio_console1,logfile=virtio_console1.log,logappend=on \
                    -device virtconsole,chardev=virtio_console1,name=org.openqa.console.virtio_console1\
                    -chardev socket,path=qmp_socket,server=on,wait=off,id=qmp_socket,logfile=qmp_socket.log,logappend=on\

