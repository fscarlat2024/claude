# Pune extrasul PDF in folderul "extrase", apoi: click dreapta pe acest fisier -> "Run with PowerShell".
# Scriptul citeste extrasele noi, scrie factura Grow LLC langa extras si o deschide.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Python: lansatorul "py" sau "python"
$python = $null
foreach ($cmd in @("py", "python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $python = $cmd; break }
}
if (-not $python) {
    Write-Host "Nu gasesc Python. Instaleaza-l de pe https://www.python.org (bifeaza 'Add Python to PATH')." -ForegroundColor Red
    Read-Host "Apasa Enter ca sa inchizi"
    exit 1
}

# Bibliotecile necesare (doar prima data dureaza)
& $python -c "import reportlab, pdfplumber" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalez bibliotecile necesare (o singura data)..."
    & $python -m pip install -q -r requirements.txt
}

$inainte = @(Get-ChildItem -Path . -Recurse -Filter "Invoice_*.pdf" | ForEach-Object FullName)

& $python agent_facturi.py --o-data

$noi = @(Get-ChildItem -Path . -Recurse -Filter "Invoice_*.pdf" |
         Where-Object { $inainte -notcontains $_.FullName })
if ($noi.Count -gt 0) {
    Write-Host ""
    Write-Host "Facturi create:" -ForegroundColor Green
    foreach ($f in $noi) {
        Write-Host "  $($f.FullName)" -ForegroundColor Green
        Invoke-Item $f.FullName
    }
}

Write-Host ""
Read-Host "Gata. Apasa Enter ca sa inchizi"
