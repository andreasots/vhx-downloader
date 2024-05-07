FROM docker.io/python:3.11.9-alpine3.19 AS builder

RUN pip install pipenv

ADD Pipfile Pipfile.lock vhx-downloader.py /usr/src/

WORKDIR /usr/src

RUN pip install pipenv

ENV PIPENV_VENV_IN_PROJECT=1
RUN pipenv sync


FROM docker.io/python:3.11.9-alpine3.19

RUN apk add tini ffmpeg

COPY --from=builder /usr/src/ /usr/src/

WORKDIR /usr/src/

ENTRYPOINT [ "tini", "--" ]
