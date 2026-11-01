# Put the Anthropic API key into the stack's Secrets Manager secret (modelProvider=anthropic).
# Run it yourself: the key is typed into a hidden prompt, written to a temporary file readable only by
# you, sent to Secrets Manager, and the file is deleted. It never appears in code, templates or chat.
#
#   powershell -ExecutionPolicy Bypass -File scripts\aws\set_anthropic_key.ps1
param([string]$Stack = "TrustedRapp", [string]$Region = "us-east-1")
$ErrorActionPreference = "Stop"
$arn = aws cloudformation describe-stacks --stack-name $Stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='AnthropicKeySecretArn'].OutputValue" --output text
if (-not $arn -or $arn -eq "None") { throw "Stack $Stack has no AnthropicKeySecretArn output (deployed with modelProvider=anthropic?)" }
$secure = Read-Host "Anthropic API key (input hidden)" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
if (-not $plain.StartsWith("sk-ant-")) { throw "That does not look like an Anthropic API key (sk-ant-...)" }
$tmp = New-TemporaryFile
try {
    [IO.File]::WriteAllText($tmp.FullName, $plain)
    aws secretsmanager put-secret-value --secret-id $arn --region $Region --secret-string "file://$($tmp.FullName)" --query VersionId --output text | Out-Null
    Write-Host "Key stored in $arn. Restart the agents' host service to pick it up:"
    Write-Host "  aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:aws:cloudformation:stack-name,Values=$Stack --parameters commands='systemctl restart trusted-rapp'"
} finally {
    Remove-Item $tmp.FullName -Force
    $plain = $null
}
