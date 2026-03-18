param(
  [Parameter(Mandatory = $true)]
  [string]$StackName,

  [Parameter(Mandatory = $false)]
  [string]$Region = "eu-west-1"
)

$ErrorActionPreference = "Stop"

$rootPath = Split-Path -Parent $PSScriptRoot
$frontendSource = Join-Path $rootPath "frontend"
$tempPath = Join-Path $rootPath "frontend-dist"

if (Test-Path $tempPath) {
  Remove-Item -Recurse -Force $tempPath
}

New-Item -ItemType Directory -Path $tempPath | Out-Null
Copy-Item -Path (Join-Path $frontendSource "*") -Destination $tempPath -Recurse -Force

$stackOutputJson = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" --output json
$outputs = $stackOutputJson | ConvertFrom-Json

$bucketName = ($outputs | Where-Object { $_.OutputKey -eq "FrontendBucketName" }).OutputValue
$distributionId = ($outputs | Where-Object { $_.OutputKey -eq "CloudFrontDistributionId" }).OutputValue
$apiEndpoint = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint" }).OutputValue

if (-not $bucketName -or -not $distributionId -or -not $apiEndpoint) {
  throw "Unable to resolve required CloudFormation outputs."
}

$configPath = Join-Path $tempPath "config.js"
$configContent = Get-Content -Raw -Path $configPath
$configContent = $configContent.Replace("__CONTACT_API_URL__", $apiEndpoint)
Set-Content -Path $configPath -Value $configContent -NoNewline -Encoding utf8

aws s3 sync $tempPath "s3://$bucketName" --delete --region $Region
aws cloudfront create-invalidation --distribution-id $distributionId --paths "/*" | Out-Null

Write-Host "Frontend deployed to s3://$bucketName and CloudFront invalidation submitted."
