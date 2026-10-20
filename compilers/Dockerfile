# One ARM toolchain from compilers.tsv on top of the mutable-dev image, at
# /opt/arm-gcc. TOOLCHAIN_PATH points the makefiles at it (they set it with ?=).
# Built by tools/compiler_matrix.py images.

ARG BASE=mutable-dev
FROM ${BASE}

ARG GCC_RELEASE
# Either an archive and its sha256, or GCC_PATH: a toolchain already in the base image.
ARG GCC_URL=
ARG GCC_SHA256=
ARG GCC_PATH=
USER root
RUN set -e; \
    if [ -n "$GCC_PATH" ]; then \
      ln -s "$GCC_PATH" /opt/arm-gcc; \
    else \
      curl -fsSL --retry 5 --retry-all-errors -o /tmp/tc.tar "$GCC_URL"; \
      echo "$GCC_SHA256  /tmp/tc.tar" | sha256sum -c -; \
      mkdir -p /opt/arm-gcc; \
      tar -xf /tmp/tc.tar -C /opt/arm-gcc --strip-components=1; \
      rm -rf /tmp/tc.tar /opt/arm-gcc/share/doc /opt/arm-gcc/share/info /opt/arm-gcc/share/man; \
    fi; \
    /opt/arm-gcc/bin/arm-none-eabi-gcc --version | sed -n 1p

ENV TOOLCHAIN_PATH=/opt/arm-gcc/ \
    MUTABLE_GCC_RELEASE=${GCC_RELEASE}

LABEL org.opencontainers.image.title="mutable-dev-gcc" \
      org.opencontainers.image.description="mutable-dev with ARM GCC ${GCC_RELEASE} at /opt/arm-gcc"
