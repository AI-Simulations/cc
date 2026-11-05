# Compatibility entry point for the Python provider switcher.
python "$PSScriptRoot\ccx-switch.py" @args
if ($LASTEXITCODE -ne 0) { throw 'Provider switch failed.' }
$apply = Join-Path $PSScriptRoot 'apply.ps1'
if (Test-Path -LiteralPath $apply) {
    try { . $apply }
    finally { Remove-Item -LiteralPath $apply -ErrorAction SilentlyContinue }
}
