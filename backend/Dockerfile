# Cloud Run: plain Docker + uvicorn. No Lambda Web Adapter, no SAM/Mangum.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects PORT at container start; it is NOT necessarily 8080.
# Shell form (not exec-array form) so `$PORT` is expanded at runtime.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
