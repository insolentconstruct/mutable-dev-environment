# ARM GCC 16 built from source: Arm publishes no GCC 16 toolchain yet. The
# configuration follows the Arch Linux arm-none-eabi packages (binutils, gcc,
# newlib and newlib-nano, rmprofile multilibs). The result goes to /opt/arm-gcc
# on top of the mutable-dev image, like the prebuilt toolchains in Dockerfile.

ARG BASE=mutable-dev

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS build

ARG DEBIAN_SNAPSHOT=20260916T000000Z
RUN rm -f /etc/apt/sources.list.d/debian.sources \
 && printf '%s\n' \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" \
      "deb http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} trixie-security main" \
      > /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils build-essential flex bison texinfo \
        libgmp-dev libmpfr-dev libmpc-dev libisl-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

ARG BINUTILS_URL=https://ftp.gnu.org/gnu/binutils/binutils-2.47.tar.xz
ARG BINUTILS_SHA256=154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff
ARG GCC_URL=https://ftp.gnu.org/gnu/gcc/gcc-16.2.0/gcc-16.2.0.tar.xz
ARG GCC_SHA256=e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e
ARG NEWLIB_URL=https://sourceware.org/pub/newlib/newlib-4.6.0.20260123.tar.gz
ARG NEWLIB_SHA256=6ff27e3bf022666f43f7802255be680eeff722ac181b1725d21e2e8318604ee3
RUN set -e; mkdir -p /build; cd /build; \
    fetch() { curl -fsSL --retry 5 -o src.tar "$1"; echo "$2  src.tar" | sha256sum -c -; \
              mkdir "$3"; tar -xf src.tar -C "$3" --strip-components=1; rm src.tar; }; \
    fetch "$BINUTILS_URL" "$BINUTILS_SHA256" binutils; \
    fetch "$GCC_URL" "$GCC_SHA256" gcc; \
    fetch "$NEWLIB_URL" "$NEWLIB_SHA256" newlib

ENV PREFIX=/opt/arm-gcc \
    TARGET=arm-none-eabi \
    PATH=/opt/arm-gcc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CFLAGS_FOR_TARGET="-g -O2 -ffunction-sections -fdata-sections" \
    CXXFLAGS_FOR_TARGET="-g -O2 -ffunction-sections -fdata-sections"

RUN set -e; mkdir /build/b-binutils; cd /build/b-binutils; \
    ../binutils/configure --target=$TARGET --prefix=$PREFIX --enable-multilib --enable-interwork \
      --enable-plugins --disable-nls --disable-shared --disable-threads --disable-werror \
      --with-gnu-as --with-gnu-ld; \
    make -j"$(nproc)"; make install-strip

# The C compiler alone, to build newlib with.
RUN set -e; mkdir /build/b-gcc1; cd /build/b-gcc1; \
    ../gcc/configure --target=$TARGET --prefix=$PREFIX --with-multilib-list=rmprofile \
      --enable-languages=c --without-headers --with-newlib --disable-nls --disable-shared \
      --disable-threads --disable-tls --disable-libssp --disable-libgomp --disable-libquadmath \
      --disable-decimal-float --disable-libffi --with-gnu-as --with-gnu-ld; \
    make -j"$(nproc)" all-gcc; make install-gcc

ARG NEWLIB_COMMON="--disable-newlib-supplied-syscalls --disable-nls"
RUN set -e; mkdir /build/b-newlib; cd /build/b-newlib; \
    ../newlib/configure --target=$TARGET --prefix=$PREFIX $NEWLIB_COMMON \
      --enable-newlib-io-long-long --enable-newlib-register-fini; \
    make -j"$(nproc)"; make install

# newlib-nano, as libc_nano.a and libg_nano.a next to the full libraries (for nano.specs).
RUN set -e; mkdir /build/b-nano; cd /build/b-nano; \
    ../newlib/configure --target=$TARGET --prefix=/build/nano $NEWLIB_COMMON \
      --enable-newlib-reent-small --disable-newlib-fvwrite-in-streamio \
      --disable-newlib-fseek-optimization --disable-newlib-wide-orient --enable-newlib-nano-malloc \
      --disable-newlib-unbuf-stream-opt --enable-lite-exit --enable-newlib-global-atexit \
      --enable-newlib-nano-formatted-io \
      CFLAGS_FOR_TARGET="-g -Os -ffunction-sections -fdata-sections"; \
    make -j"$(nproc)"; make install; \
    cd /build/nano/$TARGET/lib; \
    find . -name 'libc.a' -o -name 'libg.a' -o -name 'librdimon.a' | while read -r f; do \
      cp "$f" "$PREFIX/$TARGET/lib/${f%.a}_nano.a"; done; \
    mkdir -p $PREFIX/$TARGET/include/newlib-nano; \
    cp /build/nano/$TARGET/include/newlib.h $PREFIX/$TARGET/include/newlib-nano/

RUN set -e; mkdir /build/b-gcc; cd /build/b-gcc; \
    ../gcc/configure --target=$TARGET --prefix=$PREFIX --with-multilib-list=rmprofile \
      --enable-languages=c,c++ --enable-plugins --with-newlib --with-headers=$PREFIX/$TARGET/include \
      --disable-decimal-float --disable-libffi --disable-libgomp --disable-libmudflap \
      --disable-libquadmath --disable-libssp --disable-libstdcxx-pch --disable-nls --disable-shared \
      --disable-threads --disable-tls --with-gnu-as --with-gnu-ld --with-system-zlib \
      --with-gmp --with-mpfr --with-mpc --with-isl --enable-gnu-indirect-function \
      --with-host-libstdcxx='-static-libgcc -Wl,-Bstatic,-lstdc++,-Bdynamic -lm' \
      --with-pkgversion='mutable-dev'; \
    make -j"$(nproc)" INHIBIT_LIBC_CFLAGS='-DUSE_TM_CLONE_REGISTRY=0'; make install-strip; \
    cd $PREFIX/$TARGET/lib; \
    find . -name 'libstdc++.a' -o -name 'libsupc++.a' | while read -r f; do \
      ln -s "${f##*/}" "${f%.a}_nano.a"; done; \
    # Debug info in the target libraries only grows the image; the code is unchanged.
    find $PREFIX/$TARGET/lib $PREFIX/lib/gcc -name '*.a' -exec $TARGET-strip --strip-debug {} +; \
    rm -rf /opt/arm-gcc/share/doc /opt/arm-gcc/share/info /opt/arm-gcc/share/man; \
    arm-none-eabi-gcc --version | sed -n 1p

FROM ${BASE}
# The compiler links the build stage's gmp, mpfr, mpc and isl.
COPY --from=build /usr/lib/x86_64-linux-gnu/libgmp.so.10* /usr/lib/x86_64-linux-gnu/libmpfr.so.6* \
     /usr/lib/x86_64-linux-gnu/libmpc.so.3* /usr/lib/x86_64-linux-gnu/libisl.so.23* /usr/lib/x86_64-linux-gnu/
COPY --from=build /opt/arm-gcc /opt/arm-gcc
# nano.specs also names the C++ libraries (links to the full ones above); MI's
# firmware links with gcc, so only libc_nano is used.
RUN /opt/arm-gcc/bin/arm-none-eabi-gcc --version | sed -n 1p \
 && printf 'int main(void) { return 0; }\n' | /opt/arm-gcc/bin/arm-none-eabi-gcc -x c -mcpu=cortex-m4 \
      -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 --specs=nano.specs --specs=nosys.specs -o /dev/null -

ENV TOOLCHAIN_PATH=/opt/arm-gcc/ \
    MUTABLE_GCC_RELEASE=16.2.0

LABEL org.opencontainers.image.title="mutable-dev-gcc" \
      org.opencontainers.image.description="mutable-dev with ARM GCC 16.2.0, built from source, at /opt/arm-gcc"
