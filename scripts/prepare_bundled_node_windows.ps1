# Download and verify the pinned official Node runtime used by the Windows Desktop Native Context bundle.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("win32-x64", "win32-arm64")][string]$Platform,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = "Stop"
$NodeVersion = "v24.21.0"
if ($Platform -eq "win32-x64") {
    $NodeArch = "x64"
    $ExpectedMachine = 0x8664
    $ExpectedSha256 = "158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541"
} else {
    $NodeArch = "arm64"
    $ExpectedMachine = 0xAA64
    $ExpectedSha256 = "8779b1bde1d39f8d420e3b57aa657b39891af434d3de44a919044cec06785921"
}

function Get-PeMachine([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) {
        throw "not a PE image: $Path"
    }
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($peOffset -lt 0 -or $peOffset -gt $bytes.Length - 6) {
        throw "invalid PE header offset: $Path"
    }
    if (
        $bytes[$peOffset] -ne 0x50 -or
        $bytes[$peOffset + 1] -ne 0x45 -or
        $bytes[$peOffset + 2] -ne 0x00 -or
        $bytes[$peOffset + 3] -ne 0x00
    ) {
        throw "invalid PE signature: $Path"
    }
    return [BitConverter]::ToUInt16($bytes, $peOffset + 4)
}

$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$NodeDist = "node-$NodeVersion-win-$NodeArch"
$Archive = Join-Path $OutputDir "$NodeDist.zip"
$ExtractRoot = Join-Path $OutputDir "extract"
$NodeBin = Join-Path $ExtractRoot "$NodeDist\node.exe"

$archiveValid = $false
if (Test-Path -LiteralPath $Archive -PathType Leaf) {
    $archiveValid = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $ExpectedSha256
}
if (-not $archiveValid) {
    Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
    $partial = "$Archive.partial"
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/$NodeVersion/$NodeDist.zip" -OutFile $partial
    $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        throw "bundled Node checksum mismatch: expected=$ExpectedSha256 actual=$actual"
    }
    Move-Item -LiteralPath $partial -Destination $Archive
}

Remove-Item -LiteralPath $ExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath $Archive -DestinationPath $ExtractRoot
if (-not (Test-Path -LiteralPath $NodeBin -PathType Leaf)) {
    throw "bundled Node executable missing after extraction: $NodeBin"
}
$versionOutput = @(& $NodeBin --version)
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0 -or $versionOutput.Count -ne 1 -or $versionOutput[0].TrimEnd() -ne $NodeVersion) {
    throw "bundled Node version mismatch"
}
$machine = Get-PeMachine $NodeBin
if ($machine -ne $ExpectedMachine) {
    throw ("bundled Node architecture mismatch: expected 0x{0:x4}, got 0x{1:x4}" -f $ExpectedMachine, $machine)
}
Write-Output ([System.IO.Path]::GetFullPath($NodeBin))
