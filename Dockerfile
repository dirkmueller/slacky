FROM registry.suse.com/bci/python:3.11

RUN zypper --non-interactive addrepo --no-gpgcheck https://download.opensuse.org/repositories/SUSE:/CA/15.6/ SUSE_CA \
    && zypper --gpg-auto-import-keys refresh \
    && zypper --non-interactive in ca-certificates-suse \
    && zypper clean --all

WORKDIR /app
COPY LICENSE pyproject.toml .
COPY slacky slacky
ENV SLACKY_CONFIG="/app/config/slacky"
ENV STATE_PATH="/app/slacky/state"

RUN useradd -U --uid 1000 --shell /bin/bash -d /app app && \
    chown -R app: /app
USER app
ENV PATH="/app/.local/bin:$PATH"
RUN pipx install .
CMD ["slacky"]
