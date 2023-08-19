FROM python:3.11

COPY . /usr/src/app/
WORKDIR /usr/src/app/
RUN pip install -r requirements.txt
RUN apt update && apt install -y ffmpeg
ENV PYTHONPATH "${PYTHONPATH}:/usr/src/app/"
CMD python main.py
