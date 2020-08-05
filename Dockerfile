FROM python:3.8

WORKDIR /app

RUN pip install --upgrade pip

COPY ./requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
