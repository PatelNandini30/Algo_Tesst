FROM python:3.11

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

COPY . .

RUN mkdir -p /data/cleaned_csvs /data/expiryData /data/strikeData /data/Filter

ENV UVICORN_LOOP=uvloop
ENV UVICORN_WORKERS=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --loop $UVICORN_LOOP --workers $UVICORN_WORKERS"]
