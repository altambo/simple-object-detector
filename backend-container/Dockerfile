# Dockerfile for the SOD_processImage container Lambda.
# Normally built by `sam build --use-container` (see deploy.sh).
# Manual build on Apple Silicon:
# docker build --platform linux/amd64 --provenance false -f Dockerfile -t ultralytics-runtime:latest .
# Run locally (Lambda runtime interface on port 8080):
# docker run -e TARGET_BUCKET=<uploads-bucket> -e DATABASE_NAME=<table-name> --platform linux/amd64 -p 8080:8080 ultralytics-runtime:latest


FROM public.ecr.aws/lambda/python:3.14

RUN dnf update -y && \
    dnf install -y \
    mesa-libGL \
    libglvnd-glx \
    libX11 \
    libXext \
    && dnf clean all

RUN pip install --no-cache-dir \
    ultralytics \
    opencv-python-headless \
    numpy

RUN pip install --no-cache-dir onnx onnxruntime ultralytics --no-deps

# Copy your local model weights and the lambda handler function
COPY yolov8m.onnx ${LAMBDA_TASK_ROOT}
COPY app.py ${LAMBDA_TASK_ROOT}     

WORKDIR ${LAMBDA_TASK_ROOT}

CMD ["app.lambda_handler"]
