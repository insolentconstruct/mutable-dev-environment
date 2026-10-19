# Mutable Instruments development environment: the Vagrant VM's tools in one
# pinned linux/amd64 image. Every download is checked against a sha256.

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

ARG DEBIAN_SNAPSHOT=20260916T000000Z
# avr-gcc comes from bookworm: 5.4 still defaults to C++98, which Edges' tables need.
RUN rm -f /etc/apt/sources.list.d/debian.sources \
 && printf '%s\n' \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie-updates main" \
      "deb http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} trixie-security main" \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} bookworm main" \
      > /etc/apt/sources.list \
 && printf '%s\n' 'Package: *' 'Pin: release n=bookworm' 'Pin-Priority: 100' > /etc/apt/preferences.d/bookworm \
 && dpkg --add-architecture i386 \
 && apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl bzip2 git make gawk \
        libc6:i386 libstdc++6:i386 zlib1g:i386 gdb-multiarch \
        python3 python3-numpy python3-scipy python3-matplotlib python-is-python3 python3-serial \
        gcc-avr/bookworm binutils-avr/bookworm avr-libc/bookworm avrdude \
        openocd stlink-tools usbutils \
 && rm -rf /var/lib/apt/lists/* \
 && dpkg-query -W -f='${binary:Package} ${Version}\n' | LC_ALL=C sort > /usr/local/share/mutable-dev-packages.txt \
 && ln -s /usr /usr/local/CrossPack-AVR

# Python 3 for the makefiles' `python` (resource compiler, audio encoders);
# python-is-python3 makes `python` run it. numpy, scipy and matplotlib (pylab)
# are what the resources.py scripts import.
RUN MPLBACKEND=Agg python -c 'import scipy.signal, scipy.stats, pylab' \
 && python -c 'import platform, numpy, scipy, matplotlib; print("python %s, numpy %s, scipy %s, matplotlib %s" % (platform.python_version(), numpy.__version__, scipy.__version__, matplotlib.__version__))'

# ARM toolchains at the paths the makefiles expect: 4.8 for most modules
# (stmlib's default /usr/local/arm/ is a link to it), 4.9 for Beads.
ARG GCC48_URL=https://launchpadlibrarian.net/160488069/gcc-arm-none-eabi-4_8-2013q4-20131204-linux.tar.bz2
ARG GCC48_SHA256=fd090320ab9d4b6cf8cdf29bf5b046db816da9e6738eb282b9cf2321ecf6356a
ARG GCC49_URL=https://launchpad.net/gcc-arm-embedded/4.9/4.9-2015-q2-update/+download/gcc-arm-none-eabi-4_9-2015q2-20150609-linux.tar.bz2
ARG GCC49_SHA256=3a574c5becfca496bafe07bbd76e9deae1fd0cc99c5a9de0f8c610750f2f89ac
RUN set -e; \
    fetch() { curl -fsSL --retry 5 -o /tmp/tc.tar.bz2 "$1"; echo "$2  /tmp/tc.tar.bz2" | sha256sum -c -; \
              mkdir -p "$3"; tar -xjf /tmp/tc.tar.bz2 -C "$3" --strip-components=1; rm -f /tmp/tc.tar.bz2; }; \
    fetch "$GCC48_URL" "$GCC48_SHA256" /usr/local/arm-4.8.3; \
    fetch "$GCC49_URL" "$GCC49_SHA256" /usr/local/arm-4.9; \
    ln -s /usr/local/arm-4.8.3 /usr/local/arm; \
    /usr/local/arm/bin/arm-none-eabi-gcc --version | sed -n 1p; \
    /usr/local/arm-4.9/bin/arm-none-eabi-gcc --version | sed -n 1p; \
    avr-gcc --version | sed -n 1p

ENV OPENOCD_SCRIPT_PATH=/usr/share/openocd/scripts/ \
    PYTHONPATH=. \
    LC_ALL=C.UTF-8 \
    MPLBACKEND=Agg \
    HOME=/tmp

LABEL org.opencontainers.image.title="mutable-dev" \
      org.opencontainers.image.description="Build environment for Mutable Instruments module firmware"

WORKDIR /src
