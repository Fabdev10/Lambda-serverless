# AWS Serverless Contact App

Simple serverless web application on AWS with a static frontend on Amazon S3, an HTTP endpoint on API Gateway, a Python Lambda function, and Amazon SNS email notifications.

## Architecture

```text
Browser
  -> CloudFront
    -> S3 static website assets
  -> API Gateway HTTP API
    -> Lambda (Python)
      -> DynamoDB table (submission archive)
      -> SNS topic
        -> Email notification
```

## Features

- Static frontend served from Amazon S3 through CloudFront.
- Contact form with name, email, and message fields.
- API Gateway HTTP API endpoint for form submissions.
- Lambda function in Python with payload validation.
- Honeypot anti-spam protection on frontend and Lambda.
- Per-IP rate limiting (10 requests/minute) backed by DynamoDB with TTL.
- DynamoDB persistence for every valid submission, with a GSI for sorted queries.
- Admin API endpoint to list recent submissions with token-based access.
- Admin endpoint to delete individual submissions (`DELETE /submissions/{id}`).
- Health check endpoint (`GET /health`) for monitoring.
- SNS topic that sends an email notification for each valid submission.
- CloudWatch alarms for Lambda errors and throttles (delivered via the same SNS topic).
- AWS X-Ray active tracing on all Lambda invocations.
- Frontend admin panel: search by name/email, delete rows, export visible results to CSV.
- AWS SAM template for infrastructure as code.
- GitHub Actions for CI (tests + SAM validation) and deployment.

## Repository Layout

```text
.
|-- .github/workflows/
|   |-- ci.yml
|   `-- deploy.yml
|-- events/
|   `-- contact-form.json
|-- frontend/
|   |-- app.js
|   |-- config.js
|   |-- index.html
|   `-- styles.css
|-- scripts/
|   `-- deploy-frontend.ps1
|-- src/contact_handler/
|   |-- app.py
|   `-- requirements.txt
|-- tests/unit/
|   `-- test_app.py
|-- .gitignore
|-- README.md
|-- samconfig.toml
`-- template.yaml
```

## AWS Services Used

- Amazon S3: stores the static frontend files.
- Amazon CloudFront: serves the frontend over HTTPS.
- Amazon API Gateway HTTP API: exposes the `POST /contact`, `GET /submissions`, `DELETE /submissions/{id}`, and `GET /health` endpoints.
- AWS Lambda: validates payloads, enforces rate limits, stores submissions on DynamoDB, publishes to SNS, and serves admin endpoints.
- Amazon DynamoDB: stores contact submissions (with a GSI for sorted listing) and per-IP rate-limit counters (with TTL).
- Amazon SNS: sends email notifications for every submission and receives CloudWatch alarm notifications.
- AWS X-Ray: distributed tracing for all Lambda invocations.
- Amazon CloudWatch: error and throttle alarms for the Lambda function.

## Prerequisites

- AWS account with permissions to deploy CloudFormation, Lambda, API Gateway, SNS, S3, and CloudFront.
- AWS CLI configured locally.
- AWS SAM CLI installed locally.
- Python 3.12 for local tests.
- PowerShell if you want to use the provided frontend deployment script on Windows.

## Deploy The Stack

Build and deploy the backend infrastructure with AWS SAM:

```powershell
sam build --template-file template.yaml
sam deploy --guided
```

Recommended answers for the guided deploy:

- Stack name: `contact-webapp`
- AWS region: `eu-west-1` or another supported region
- Parameter `NotificationEmail`: the email address that should receive the SNS notification
- Parameter `AdminToken`: secret token used to protect `GET /submissions`
- Allow SAM CLI IAM role creation: `Y`

After deployment, confirm the SNS subscription from the email sent by AWS. Until the subscription is confirmed, notifications will not arrive.

## Deploy The Frontend

The frontend uses a placeholder API URL in `frontend/config.js`. The deployment script replaces that placeholder with the real API Gateway endpoint, uploads the files to S3, and invalidates CloudFront.

```powershell
.\scripts\deploy-frontend.ps1 -StackName contact-webapp -Region eu-west-1
```

You can then open the CloudFront URL from the stack outputs:

```powershell
aws cloudformation describe-stacks `
  --stack-name contact-webapp `
  --region eu-west-1 `
  --query "Stacks[0].Outputs[?OutputKey=='FrontendUrl'].OutputValue" `
  --output text
```

The frontend now includes an admin panel in the same page. To list submissions:

- Open the deployed frontend URL.
- In the "Admin area" section, paste your `AdminToken` in the token field.
- Choose a `limit` (1-100), select a time range, and click `Load submissions`.
- Use the **Search** box to filter visible rows by name or email (client-side).
- Click **Delete** on a row to permanently remove that submission via `DELETE /submissions/{id}`.
- Click **Export CSV** to download the currently visible results as a CSV file.
- Use `Load more` to request the next page from DynamoDB.

The admin panel calls `GET /submissions` using header `x-admin-token`, masks emails in the UI, and filters rows client-side by time range.

## Local Testing

Run the Lambda unit tests:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Invoke the Lambda locally with the sample event:

```powershell
sam local invoke ContactFunction --event events/contact-form.json
```

If you want to test the full HTTP flow locally:

```powershell
sam local start-api
```

Then send a request to `http://127.0.0.1:3000/contact` with the same JSON shape used by the frontend.

## API Contract

Request body:

```json
{
  "name": "Fabio Rossi",
  "email": "fabio@example.com",
  "message": "Ciao, vorrei ricevere maggiori dettagli.",
  "website": ""
}
```

`website` is an optional hidden honeypot field used for bot detection. Real users leave it empty. If it is filled, Lambda returns a generic success response and skips SNS publishing.

Success response:

```json
{
  "message": "Message received successfully."
}
```

Admin listing endpoint:

- Method: `GET`
- Path: `/submissions`
- Header required: `x-admin-token: <AdminToken>`
- Optional query parameter: `limit` (default `20`, max `100`)
- Optional query parameter: `cursor` (pagination token from previous response)
- Results are sorted newest-first via a DynamoDB GSI on `created_at`.

Delete submission endpoint:

- Method: `DELETE`
- Path: `/submissions/{submission_id}`
- Header required: `x-admin-token: <AdminToken>`

Health check endpoint:

- Method: `GET`
- Path: `/health`
- No authentication required.

Admin success response:

```json
{
  "count": 2,
  "next_cursor": "eyJzdWJtaXNzaW9uX2lkIjogIjliYmJkM2U4LTkzYjItNGY0NS1hNmI5LTMwNTJmNWM2NjFjZCJ9",
  "items": [
    {
      "submission_id": "9bbbd3e8-93b2-4f45-a6b9-3052f5c661cd",
      "created_at": "2026-04-17T10:12:04.531481+00:00",
      "name": "Fabio Rossi",
      "email": "fabio@example.com",
      "message": "Ciao, avrei bisogno di una consulenza.",
      "source_ip": "203.0.113.10",
      "user_agent": "Mozilla/5.0"
    }
  ]
}
```

When `next_cursor` is empty, there are no additional pages.

Unauthorized admin response:

```json
{
  "error": "Unauthorized."
}
```

Validation error response:

```json
{
  "error": "Validation failed.",
  "details": [
    {
      "field": "email",
      "message": "Email format is invalid."
    }
  ]
}
```

## GitHub Actions

### CI workflow

`.github/workflows/ci.yml` runs:

- Python dependency install
- Lambda unit tests
- `sam validate`
- `sam build`

### Deploy workflow

`.github/workflows/deploy.yml` is manually triggered and expects:

- `AWS_DEPLOY_ROLE_ARN` in repository secrets
- OIDC enabled between GitHub Actions and AWS
- workflow inputs for region, stack name, and notification email

The workflow deploys the stack, resolves the CloudFormation outputs, injects the API URL into the frontend, uploads files to S3, and creates a CloudFront invalidation.

## Important Notes

- The SNS email subscription must be confirmed manually.
- API Gateway CORS is restricted to the CloudFront domain created by the stack.
- The first frontend deployment must happen after the stack exists, because it depends on the generated API endpoint and bucket output.
- Bucket names must be globally unique. If the chosen `ProjectName` collides, change it during deployment.
- Keep `AdminToken` secret. Rotate it periodically by updating the stack parameter.

## Next Improvements

- Add a custom domain with ACM and Route 53.
- Add AWS WAF managed bot control and API rate limiting.
- Add structured logging and metrics with AWS Lambda Powertools.
- Add integration tests against a deployed environment.