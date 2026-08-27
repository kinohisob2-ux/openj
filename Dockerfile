FROM python:3.11-slim

WORKDIR /app

# Tizim kutubxonalarini o'rnatish
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Python kutubxonalarini o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Loyihani nusxalash
COPY . .

# Botni ishga tushirish
CMD ["python", "bot.py"]
