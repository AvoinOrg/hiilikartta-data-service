FROM python:3.9.15

SHELL ["/bin/bash", "-l", "-c"]

WORKDIR /app

RUN apt-get update &&\ 
    apt-get install autossh -y &&\
    pip install poetry &&\
    printf "  PasswordAuthentication yes\n  KbdInteractiveAuthentication yes" >> /etc/ssh/ssh_config &&\
    sed -i '1,6d' /root/.bashrc &&\
    echo "source \"\$(poetry env info --path)/bin/activate\"" >> /root/.bashrc
