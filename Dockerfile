FROM python:3.8

WORKDIR /app

RUN pip install --upgrade pip

COPY ./test-requires.txt /tmp/test-requires.txt
RUN pip install -r /tmp/test-requires.txt
