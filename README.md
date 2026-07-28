# SimpleObjectDetector: Full-Stack Serverless Object Detection Pipeline

SimpleObjectDetector is a high-performance, asynchronous full-stack serverless web application that utilizes machine learning to process and identify objects inside images. It uses a decoupled modern architecture combining an optimized static web frontend with a globally distributed, event-driven backend.

## 🏗️ Architecture Blueprint

### 🌐 Frontend (Static JAMstack Site)
- **`index.html`**: Native, lightweight UI providing smooth processing updates, automatic client geolocation tracking via `ipapi.co`, and reactive state selectors.
- **`main.js`**: Pure vanilla JavaScript orchestrating async image uploads (supporting local base64 transformations or remote image web URLs), dynamic polling loops, and live rendering triggers.
- **`config.js`**: Securely omitted from public tracking; injected dynamically by the automation pipeline to provide live AWS API endpoints.

### ⚡ Backend (AWS Serverless Architecture)
- **API Gateway (`SODGatewayApi`)**: Ultra-low-latency HTTP API handling route distribution and enforcing broad cross-origin resource sharing (CORS) rules.
- **`GetImageUrlFunction` (Lambda Microservice)**: Lightweight Python ingestion handler mapping ingestion inputs, logging transaction histories, and placing payloads onto queues.
- **SQS Queue (`SODQueue`)**: Durable message brokering layer decoupling ingestion from resource-heavy processing stages.
- **`ProcessImageFunction` (Container Microservice)**: High-memory Docker container running on x86_64 architectures, baking a native `yolov8m.onnx` machine learning model weights layer to process images asynchronously.
- **`SODCheckJobStatus` (Lambda Microservice)**: Dedicated polling microservice reading job states from the database.
- **DynamoDB Table (`SODJobStatusTable`)**: High-performance NoSQL data register recording analytical job progress metrics.

---

## 🛠️ Local Prerequisites

Before you attempt to build or deploy this application, ensure your local development machine has the following tools installed and configured:

1. **AWS CLI (v2)**: [Install AWS CLI](https://amazon.com) and authenticate using your access keys (`aws configure`).
2. **AWS SAM CLI**: [Install AWS SAM CLI](https://amazon.com) to validate and compile serverless resources.
3. **Docker Desktop**: Required locally. SAM uses Docker to compile and package your Python container layers inside matching container sandboxes (`sam build --use-container`).
4. **Git Large File Storage (LFS)**: Crucial for pulling down the physical binary model files.
   ```bash
   git lfs install
   git lfs pull
   ```

---

## 🔒 Security & Public Repository Safety

This repository is **completely safe for public distribution**. It uses standard identity-based IAM configurations rather than hardcoded environment passwords.

- **`.gitignore`** is configured to lock out compiled `.aws-sam/` artifacts, cache contexts, and private signing credentials.
- **`frontend/config.js`** is intentionally hidden from Git tracking. This file is generated dynamically on the fly by the deployment automation pipeline, preventing live endpoint leaking.

---

## 🚀 How to Deploy

Deploying across completely isolated environments (e.g., separating your `dev` sandbox from a production `prod` architecture) is handled entirely using a unified automation script.

### 1. Give Permissions to the Automation Script
```bash
chmod +x deploy.sh
```

### 2. Run the Deployment
Trigger the script by passing your environment target signature argument:

- **Deploying to Development (Default)**:
  ```bash
  ./deploy.sh dev
  ```
- **Deploying to Production**:
  ```bash
  ./deploy.sh prod
  ```

### 🤖 What the `deploy.sh` Pipeline Automates:
1. **Cache Purges**: Wipes local build directories (`.aws-sam/`) to prevent compiling old dependency files.
2. **Template Validation**: Asserts that `template.yaml` complies with AWS CloudFormation schemas.
3. **Rollback Remediation**: Automatically scans for and purges un-updatable infrastructure states (`ROLLBACK_COMPLETE`).
4. **Conflict S3 Destruction**: Pre-emptively wipes and deletes duplicate global buckets to bypass resource-existence blocks.
5. **Container Builds**: Packages your Python 3.14 files and bakes your functional `yolov8m.onnx` layers inside a local Docker instance.
6. **AWS Infrastructure Provisioning**: Ships assets to AWS ECR and executes an automated `sam deploy --resolve-s3` transaction block.
7. **Config Generation**: Pulls the brand new CloudFormation output metrics and auto-creates your valid `frontend/config.js` profile.
8. **Static Assets Synchronization**: Performs target bucket verification loops and syncs your optimized `index.html`, `main.js`, and `config.js` directly to your S3 static website hosting directory.

---

## 🌐 Post-Deployment Outputs

When the execution completes, the console will print your clickable production addresses instantly:

```text
====================================================
 ✅ Deployment Completed! [prod] website is live.
 🌐 Website Root URL: http://amazonaws.com
 📄 Direct Index URL: http://amazonaws.com/index.html
 ⚡ API Endpoint:     https://amazonaws.com
====================================================
```

Open the **`Direct Index URL`** in any web browser to interact with your live environment!

