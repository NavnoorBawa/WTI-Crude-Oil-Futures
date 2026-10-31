# The reproduce environment, pinned (docs/ROADMAP.md, Stage 1, item 6).
#
#   docker build -t wti-vol .
#   docker run --rm wti-vol make reproduce       # rebuilds data/vol_lab.json and compares (about 4 minutes)
#   docker run --rm wti-vol make test            # or lint, type, check-frontend, figures (see the Makefile)
#
# Both base images are pinned by digest, and each was at least 14 days old when pinned (the
# dependency policy in CLAUDE.md; Dependabot proposes digest updates with the same cooldown). The
# Python and npm packages come from the repository's own pins, requirements*.txt and
# package-lock.json, never from a second list. The numeric environment variables are the ones the
# Reproduce workflow sets (.github/workflows/reproduce.yml), so the rebuild runs the same kernels.
# Bit for bit is checked on x86-64; on other architectures backend/vol_lab.py's compare_reports
# decides, within the published rounding.

# Node 24.21.0 on Debian bookworm, published 2026-10-06: only node and npm are taken from it.
FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS node

# Python 3.12.14 on Debian bookworm, published 2026-09-19 (CI runs Python 3.12).
FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# libgomp1 is the OpenMP runtime LightGBM's wheel links against (it runs one thread here, as in CI).
# make runs the targets and git lets the figures target show drift; neither computes a number.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 make git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_CORETYPE=Haswell \
    NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=. \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /work

COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --retries 5 --timeout 60 -r requirements.txt -r requirements-dev.txt \
    && python -m pip check

# Lifecycle scripts stay off, as in CI.
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY . .

CMD ["make", "help"]
