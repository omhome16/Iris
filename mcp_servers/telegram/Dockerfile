FROM python:3.12-slim

WORKDIR /app
COPY server.py requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "server.py"]