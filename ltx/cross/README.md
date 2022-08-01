# Cross compiling for direct boot

The easiest way to cross compile a minimal LTX based Linux system and
direct boot it with QEMU.

## Kernel

Clang LLVM supports cross compilation out of the box. This doesn't
work well with userland due to a dependency on libgcc (see
below). However Linux doesn't require this. Using Clang with Linux is
as simple as adding `LLVM=1` to the command line.

```sh
$ cd $linux
$ make LLVM=1 ARCH=arm64 defconfig
$ make LLVM=1 ARCH=arm64 menuconfig
$ make LLVM=1 ARCH=arm64 -j$(nproc)
$ cp arch/arm64/boot/Image.gz $ltx/cross/aarch64/
```

## Userland

Userland is complicated by the libc's their dependency on the compiler
runtime library (compiler-rt, libgcc). LLVM has it's own compiler-rt,
but it is missing features that are implemnted by libgcc. Cross
compiling GCC is a farce, hence why we are using Clang in the first
instance.

Luckily the Zig language bundles Clang, its own compiler-rt and some
libc's (e..g musl, glibc). Its compiler-rt is incomplete as well
however it doesn't seem to matter for our purposes.

It appears that Zig compiles only the parts of libc required for our
application from source. This is only a small subset of musl which
doesn't include some floating point functionality which is missing
from the LLVM's and Zig's runtime libraries.

Also even though the LTX executable produced by Zig is statically
compiled. It is relatively small at 126K. This is double the size
compared to being dynamically linked to musl. However we can live with
this.

### LTX

```sh
$ cd $runltp-ng/ltx
$ zig cc --target=aarch64-linux-musl -o ltx ltx.c
$ cp ltx cross/initrd/init
```

### LTP

My first attempt at cross compiling LTP with Zig has not been entirely
successful. However it appears that it can compile most tests. This is
good enough for now and also a pleasant surprise.

```sh
$ cd $ltp
$ make autotools
$ ./configure --prefix=(realpath ../ltp-install/) CC='zig cc --target=aarch64-linux-musl' --host=aarch64
$ make -j$(nproc)
```

Tests executables can be copy and pasted into `$ltx/cross/initrd/bin`
or similar.

## initrd

Unless we embed init (LTX) inside the kernel image. We need an initial
system image which the kernel can load init from. This can be created
with `cpio`.

```sh
$ cd $ltx/cross/initrd
$ find . | cpio -H newc -o | gzip -n > ../aarch64/initrd.cpio.gz
```

## Booting with QEMU

The kernel and initramfs can be direct booted by QEMU

```sh
$ qemu-system-aarch64 -m 1G \
                      -smp 2 \
                      -display none \
                      -kernel aarch64/Image \
                      -initrd aarch64/initrd.cpio.gz \
                      -machine virt -cpu cortex-a57 \
                      -serial stdio \
                      -append 'console=ttyAMA0 earlyprintk=ttyAMA0'
```
