FROM python:3.11

COPY . /usr/src/app/
WORKDIR /usr/src/app/
RUN pip install -r requirements.txt
RUN apt update && apt install -y ffmpeg
CMD python main.py
