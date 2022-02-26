FROM python:3.8.12-buster

RUN mkdir /app
WORKDIR /app

COPY . /app/

RUN apt-get install autoconf automake libtool
RUN chmod +x install-coap-client.sh
RUN ./install-coap-client.sh
RUN pip install -r requirements.txt

CMD ["python3", "main.py"]