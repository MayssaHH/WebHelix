FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
RUN apt-get update -qq && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 && rm -rf /var/lib/apt/lists/*
COPY opencode /usr/local/bin/opencode
COPY wh-preview.py /usr/local/bin/wh-preview
RUN chmod 755 /usr/local/bin/opencode /usr/local/bin/wh-preview && mkdir -p /work/candidate /work/reference /work/notes /work/previews /home/agent /runtime && opencode --version
LABEL org.webhelix.opencode.version="1.19.4" org.webhelix.opencode.archive-sha256="1697ef40558d378bd2f72efa8fafe1bed1f0e664f09247b9357556b95dbc7cbc"
ENV HOME=/home/agent XDG_CONFIG_HOME=/home/agent/config XDG_CACHE_HOME=/home/agent/cache XDG_DATA_HOME=/home/agent/data XDG_STATE_HOME=/home/agent/state OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_PROJECT_CONFIG=1 OPENCODE_DISABLE_AUTOCOMPACT=1 OPENCODE_DISABLE_FFF=1 OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=1 OPENCODE_PURE=1
WORKDIR /work
CMD ["sleep", "infinity"]
