FROM python:3.9.15

SHELL ["/bin/bash", "-l", "-c"]

WORKDIR /app

RUN apt-get update &&\ 
    apt-get install autossh -y &&\
    pip install poetry &&\
    sed -i '1,6d' /root/.bashrc &&\
    echo "poetry shell" >> /root/.bashrc
