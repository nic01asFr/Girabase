FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic

COPY engine.py api.py ./
COPY static/ ./static/

EXPOSE 8090

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8090"]
