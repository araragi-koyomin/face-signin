FROM python:3.12-slim

RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads/faces uploads/signin \
    && openssl req -x509 -newkey rsa:2048 -keyout /app/key.pem -out /app/cert.pem \
       -days 365 -nodes -subj "/CN=localhost"

EXPOSE 8000 8443

CMD ["python", "start.py"]
