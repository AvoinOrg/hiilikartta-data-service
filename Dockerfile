FROM python:3.9.15

WORKDIR /app

RUN apt-get update &&\ 
    apt-get install autossh -y &&\
    pip install poetry