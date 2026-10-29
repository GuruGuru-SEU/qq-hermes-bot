FROM nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d
USER root
RUN mkdir -p /home/hermes /opt/bot && chown 1000:1000 /home/hermes \
    && ln -s /opt/hermes/tools/uv-*/uv /usr/local/bin/uv \
    && ln -s /opt/hermes/.venv /opt/bot-venv
# Browser and fonts are installed at build time; runtime remains UID 1000.
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL --connect-timeout 10 --max-time 120 --retry 2 \
      https://registry.npmjs.org/agent-browser/-/agent-browser-0.40.0.tgz -o /tmp/agent-browser.tgz \
    && echo 'baa0c0477a33c09298cd510d32bfd92984d94f52d465ab88e3dad388afcab0507140355376d9ab586593316665688210593a04b5951f5955c14924d8e9a1f3ac  /tmp/agent-browser.tgz' | sha512sum -c - \
    && mkdir -p /opt/agent-browser-cli/node_modules/agent-browser /opt/agent-browser-cli/node_modules/.bin \
    && tar -xzf /tmp/agent-browser.tgz -C /opt/agent-browser-cli/node_modules/agent-browser --strip-components=1 \
    && ln -s ../agent-browser/bin/agent-browser-linux-x64 /opt/agent-browser-cli/node_modules/.bin/agent-browser \
    && chmod 755 /opt/agent-browser-cli/node_modules/agent-browser/bin/agent-browser-linux-x64 \
    && rm /tmp/agent-browser.tgz \
    && /opt/agent-browser-cli/node_modules/.bin/agent-browser --version
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
COPY . /opt/qq-hermes-bot/
RUN printf '#!/opt/hermes/.venv/bin/python\nimport sys\nsys.path.insert(0,"/opt/qq-hermes-bot")\nfrom qqhermes.browser import main\nmain()\n' > /usr/local/bin/agent-browser \
    && chmod 755 /usr/local/bin/agent-browser
RUN /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/prepare_bundled_plugin.py \
    && /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/patch_hermes_cron.py \
    && /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/patch_hermes_delivery.py
RUN printf 'import sys\nsys.path.insert(0,"/opt/qq-hermes-bot")\nfrom qqhermes.history import main\nmain()\n' > /opt/bot/history.py
ENV PYTHONPATH=/opt/hermes:/opt/qq-hermes-bot PATH=/opt/hermes/.venv/bin:/opt/hermes/bin:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
WORKDIR /opt/hermes
ENTRYPOINT ["/opt/qq-hermes-bot/deploy/entrypoint.sh"]
CMD ["gateway", "run"]
