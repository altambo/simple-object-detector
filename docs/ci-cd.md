# CI/CD

Two GitHub Actions workflows run the pipeline:

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | Pull requests, pushes to `main`, manual runs | Unit tests, SAM template lint, container build + test |
| [`deploy.yml`](../.github/workflows/deploy.yml) | Manual only (pick `dev` or `prod`) | Runs CI, then `./deploy.sh <env>` with short-lived AWS credentials |

Nothing reaches AWS unless every CI job passes, and no AWS keys are stored in GitHub.

## CI jobs

- **Unit tests** (`tests/`): pytest on Python 3.14, the Lambda runtime version. AWS (S3, SQS, DynamoDB) is mocked with [moto](https://docs.getmoto.org), so the tests never touch a real account. The detector tests run the real `yolov8m.onnx` model on `tests/fixtures/bus.jpg`.
- **Validate SAM template**: `sam validate --lint` on `template.yaml`.
- **Container test**: [`scripts/test_container.sh`](../scripts/test_container.sh) builds the processing image and:
  1. fails if the image is over 1000 MB, to catch heavy dependencies such as PyTorch creeping back in and slowing cold starts;
  2. runs the detector inside the image on the fixture photo;
  3. starts the image with the Lambda Runtime Interface Emulator (built into the AWS base image) and invokes the handler the way Lambda does.

The model weights are stored in Git LFS. A small composite action ([`.github/actions/checkout-lfs`](../.github/actions/checkout-lfs/action.yml)) caches them, so runners don't re-download ~100 MB of LFS bandwidth on every run.

## Running the same checks locally

```bash
pip install -r requirements-dev.txt
python -m pytest            # unit tests
sam validate --lint         # template
./scripts/test_container.sh # container (needs Docker)
```

## Setting up deployments (one time)

### 1. Create the AWS role GitHub deploys with

[`infra/github-deploy-role.yaml`](../infra/github-deploy-role.yaml) creates an IAM role that only GitHub Actions jobs from this repo's `dev` and `prod` environments can assume, through OpenID Connect (OIDC).

```bash
aws cloudformation deploy \
  --template-file infra/github-deploy-role.yaml \
  --stack-name SimpleObjectDetector-github-deploy-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubRepo=<owner>/<repo>

aws cloudformation describe-stacks \
  --stack-name SimpleObjectDetector-github-deploy-role \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text
```

If your account already has a GitHub OIDC provider (`token.actions.githubusercontent.com`), add `CreateOidcProvider=false ExistingOidcProviderArn=<its ARN>` to the overrides.

The role has `PowerUserAccess` plus IAM permissions limited to roles named `SimpleObjectDetector-*`, which is what SAM needs to create the Lambda execution roles. Tighten this further if your account holds other workloads.

### 2. Configure the GitHub repository

In **Settings → Environments**:
- Create `dev` and `prod` environments.
- On `prod`, add yourself as a **required reviewer**, so production deploys wait for approval.

In **Settings → Secrets and variables → Actions → Variables**, add a repository variable:
- `AWS_DEPLOY_ROLE_ARN` = the role ARN from step 1. It is not a secret; the role can only be assumed from this repo's environments.

### 3. Deploy

**Actions → Deploy → Run workflow**, choose the branch and environment. The job runs CI first, then deploys the stack and uploads the frontend. The website URL is printed at the end of the log.

On the first CI deploy, SAM creates its own ECR repository for the container image (`--resolve-image-repos`), because the runner has no local `samconfig.toml`.

## Coming from Bitbucket Pipelines

| Bitbucket Pipelines | GitHub Actions here |
|---|---|
| `bitbucket-pipelines.yml` | `.github/workflows/*.yml` (one file per workflow) |
| `pipelines: default` / `pull-requests` | `on: push` / `on: pull_request` in `ci.yml` |
| `custom:` pipeline, run manually | `on: workflow_dispatch` in `deploy.yml` |
| `deployment: production` + deployment permissions | `environment: prod` + required reviewers |
| Repository / deployment variables | Actions variables and secrets |
| `services: docker` | Docker is available on `ubuntu-latest` runners |
| Bitbucket OIDC to AWS | GitHub OIDC to AWS (`aws-actions/configure-aws-credentials`) |
