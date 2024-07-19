FROM docker.io/python:3.13-rc-alpine

RUN mkdir /app
COPY requirements.txt /app/requirements.txt

RUN python3 -m pip install -r /app/requirements.txt

COPY renault.py /app/.

ENTRYPOINT [ "python3", "/app/renault.py"]