FROM python:3.12-slim

WORKDIR /app
COPY app.py requirements.txt ./
COPY templates ./templates
COPY static ./static
RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]