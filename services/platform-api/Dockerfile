FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements-dev.lock ./
COPY src ./src
RUN python -m pip install --no-cache-dir --constraint requirements-dev.lock .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "robot_platform_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

