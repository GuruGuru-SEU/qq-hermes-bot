FROM nousresearch/hermes-agent@sha256:c56a1bd108e94c850cc785b5c7142b6f1633066b2306f15a9438d698a3be0077
USER root
RUN mkdir -p /home/hermes /opt/bot && chown 1000:1000 /home/hermes \
    && ln -s /opt/hermes/tools/uv-*/uv /usr/local/bin/uv \
    && ln -s /opt/hermes/.venv /opt/bot-venv
COPY . /opt/qq-hermes-bot/
RUN printf 'import sys\nsys.path.insert(0,"/opt/qq-hermes-bot")\nfrom qqhermes.history import main\nmain()\n' > /opt/bot/history.py
ENV PYTHONPATH=/opt/hermes:/opt/qq-hermes-bot PATH=/opt/hermes/.venv/bin:/opt/hermes/bin:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
WORKDIR /opt/hermes
ENTRYPOINT ["/opt/qq-hermes-bot/deploy/entrypoint.sh"]
CMD ["gateway", "run"]
