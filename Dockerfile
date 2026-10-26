FROM nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d
USER root
RUN mkdir -p /home/hermes /opt/bot && chown 1000:1000 /home/hermes \
    && ln -s /opt/hermes/tools/uv-*/uv /usr/local/bin/uv \
    && ln -s /opt/hermes/.venv /opt/bot-venv
COPY . /opt/qq-hermes-bot/
RUN /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/prepare_bundled_plugin.py \
    && /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/patch_hermes_cron.py \
    && /opt/hermes/.venv/bin/python /opt/qq-hermes-bot/deploy/patch_hermes_delivery.py
RUN printf 'import sys\nsys.path.insert(0,"/opt/qq-hermes-bot")\nfrom qqhermes.history import main\nmain()\n' > /opt/bot/history.py
ENV PYTHONPATH=/opt/hermes:/opt/qq-hermes-bot PATH=/opt/hermes/.venv/bin:/opt/hermes/bin:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
WORKDIR /opt/hermes
ENTRYPOINT ["/opt/qq-hermes-bot/deploy/entrypoint.sh"]
CMD ["gateway", "run"]
