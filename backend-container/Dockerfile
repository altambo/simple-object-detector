# Dockerfile for the SOD_processImage container Lambda.
# Normally built by `sam build --use-container` (see deploy.sh).
# Manual build on Apple Silicon:
# docker build --platform linux/amd64 --provenance false -f Dockerfile -t sod-process-image:latest .
# Run locally (Lambda runtime interface on port 8080):
# docker run -e TARGET_BUCKET=<uploads-bucket> -e DATABASE_NAME=<table-name> --platform linux/amd64 -p 8080:8080 sod-process-image:latest

FROM public.ecr.aws/lambda/python:3.14

# Inference runs on onnxruntime + numpy + Pillow (no torch/ultralytics/OpenCV)
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the model weights and the lambda handler
COPY yolov8m.onnx ${LAMBDA_TASK_ROOT}
COPY detector.py app.py ${LAMBDA_TASK_ROOT}/

WORKDIR ${LAMBDA_TASK_ROOT}

CMD ["app.lambda_handler"]
