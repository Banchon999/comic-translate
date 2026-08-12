# Comic Translate on a RunPod GPU pod, reachable from any browser.
#
# There is no web version of this app to run — it is a Qt desktop program. So
# it runs against a virtual X display inside the pod, and noVNC hands that
# display to your browser over RunPod's HTTPS proxy. Nothing about the app
# changes; you are looking at the real thing, running on a real GPU.
#
# Build:
#   docker build -f runpod/Dockerfile -t <you>/comic-translate:runpod .
#   docker build -f runpod/Dockerfile --build-arg WITH_PADDLE_VL=1 -t <you>/comic-translate:runpod-vl .
#
# See runpod/README.md for the pod template settings.

FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04 ships Python 3.12, which is the version this project targets, so
# no deadsnakes PPA is needed. Every compiled dependency (mahotas,
# PhotoshopAPI, onnxruntime) publishes a cp312 manylinux wheel, so there is no
# compiler in this image either.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv \
      xvfb x11vnc x11-utils openbox novnc websockify \
      libgl1 libglib2.0-0t64 libegl1 libdbus-1-3 libfontconfig1 libxrender1 \
      libx11-xcb1 libxkbcommon-x11-0 libxcb-util1 libxcb-cursor0 \
      libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
      libxcb-render0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 \
      libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
      fonts-noto-core fonts-noto-cjk fonts-dejavu-core \
      libarchive-tools \
      ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# Opening the bare proxy URL should land you on the desktop, not on noVNC's
# connection form.
#
# resize=scale, not remote: remote resizing needs the VNC server to negotiate a
# new mode, and Xvfb only ever advertises the one it was started with. Scaling
# is client-side and always works — set RESOLUTION to your own screen size if
# you want it pixel-for-pixel.
RUN printf '%s\n' \
  '<!doctype html><meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=1&resize=scale&reconnect=1">' \
  > /usr/share/novnc/index.html

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /opt/comic-translate

# Dependencies first, so editing the app does not re-download several hundred
# megabytes of wheels on every rebuild.
COPY requirements.txt ./
RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y onnxruntime \
 && pip install --no-cache-dir onnxruntime-gpu

# The PaddleOCR-VL engine needs the whole PyTorch stack, which takes the image
# from roughly 4 GB to well over 10 GB. It is off unless you ask for it — the
# other ten OCR engines run on onnxruntime alone.
ARG WITH_PADDLE_VL=0
RUN if [ "$WITH_PADDLE_VL" = "1" ]; then \
      pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu126 \
      && pip install --no-cache-dir "transformers>=5"; \
    fi

COPY . ./
RUN chmod +x runpod/start.sh

ENV DISPLAY=:1 \
    QT_QPA_PLATFORM=xcb \
    PYTHONUNBUFFERED=1 \
    RESOLUTION=1920x1080 \
    DATA_ROOT=/workspace/comic-translate

# There is no D-Bus secret service in a container. Failing outright is what
# makes app/account/auth/token_storage.py fall back to QSettings, which lives
# on the volume; a silent no-op backend would accept the token and drop it.
ENV PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring

EXPOSE 6080

CMD ["/opt/comic-translate/runpod/start.sh"]
