# The web app: Python, the packages from requirements.txt and the project code
FROM python:3.12-slim

# Don't write .pyc files, and show print/log output right away
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Packages first: Docker reuses this layer until requirements.txt changes, so rebuilds are fast
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Development server, reachable from outside the container
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
