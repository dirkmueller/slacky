FROM registry.suse.com/bci/python:3.13

WORKDIR /app
COPY slacky.py requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/state
RUN zypper --non-interactive addrepo --no-gpgcheck https://download.opensuse.org/repositories/SUSE:/CA/openSUSE_Tumbleweed/ SUSE_CA \
    && zypper --gpg-auto-import-keys refresh \
    && zypper --non-interactive in -y ca-certificates-suse \
    && zypper clean --all

CMD [ "python3", "./slacky.py" ]

