FROM python:3.11

RUN mkdir -p /usr/src/app
COPY requirements.txt /usr/src/app/requirements.txt
WORKDIR /usr/src/app/
RUN pip install aiogram==2.25.1
RUN pip install -r requirements.txt
RUN apt update && apt install -y ffmpeg

COPY . /usr/src/app/
ENV PYTHONPATH "${PYTHONPATH}:/usr/src/app/"
CMD python main.py
