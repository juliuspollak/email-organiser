FROM python:3.12-slim
ARG BUILD_VERSION=1.0.0
ARG BUILD_ARCH=aarch64
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY organiser.py /app/organiser.py
CMD ["python", "-u", "/app/organiser.py"]
