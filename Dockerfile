FROM python:3.9

WORKDIR /app

RUN pip install --upgrade pip

COPY ./requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
