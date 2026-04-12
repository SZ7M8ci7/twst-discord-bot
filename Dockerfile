FROM python:3.11
WORKDIR /bot

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
ENV TZ Asia/Tokyo
ENV TERM xterm
ENV PYTHONUNBUFFERED 1
ENV PIP_DISABLE_PIP_VERSION_CHECK 1

# OpenCV import に必要なランタイムだけ入れる
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /bot/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /bot

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=3)"

CMD ["python", "app/main.py"]
