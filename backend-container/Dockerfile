# dockerfile for building lightweight deployment container
# syntax:(using AppleSilicon)
# docker build --platform linux/amd64 --provenance false -f Dockerfile -t ultralytics-runtime:latest .
# run:
# docker run -e BUCKET_NAME=amzn-s3-my-models -e MODEL_KEY=yolov8m.onnx --platform linux/amd64 -p 8080:8080 ultralytics-runtime:latest


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
# Note: download weights and save to /tmp folder in container.this saves about 6GB of space
# COPY yolov8m.pt ${LAMBDA_TASK_ROOT} 
COPY yolov8m.onnx ${LAMBDA_TASK_ROOT}
COPY app.py ${LAMBDA_TASK_ROOT}     

WORKDIR ${LAMBDA_TASK_ROOT}

CMD ["app.lambda_handler"]
