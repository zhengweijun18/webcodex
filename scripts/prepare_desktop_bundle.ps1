# Stage exact WebCodex runtime and zero-quota Native Context assets for a native Windows Tauri Desktop bundle.
#
# This helper consumes already-built release binaries plus one verified Node
# executable and the vendored Context Bridge, verifies exact identity and
# architecture, copies them into an ignored generated tree, proves the copies
# are byte-for-byte identical, and writes a Tauri config overlay.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BinDir,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$SourceSha,
    [Parameter(Mandatory = $true)][Int64]$BuiltAt,
    [Parameter(Mandatory = $true)][ValidateSet("win32-x64", "win32-arm64")][string]$Platform,
    [Parameter(Mandatory = $true)][string]$NodeBin,
    [Parameter(Mandatory = $true)][string]$NodeVersion,
    [Parameter(Mandatory = $true)][string]$ContextBridgeDir,
    [Parameter(Mandatory = $true)][string]$ContextBridgeVersion,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = "Stop"

$BridgeFiles = @(
    "bridge-lib.mjs",
    "readiness.mjs",
    "README.md",
    "package.json",
    "server.mjs",
    "self-check.mjs"
)

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw "invalid Desktop bundle version '$Version'"
}
if ($SourceSha -notmatch '^[0-9A-Fa-f]{40}$') {
    throw "SourceSha must be one exact 40-hex Git commit"
}
if ($BuiltAt -le 0) {
    throw "BuiltAt must be a positive Unix timestamp"
}
if ($NodeVersion -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$') {
    throw "invalid bundled Node version '$NodeVersion'"
}
if ($ContextBridgeVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw "invalid Context Bridge version '$ContextBridgeVersion'"
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

function Get-FirstOutputLine([string]$Binary, [string[]]$Arguments, [string]$Label) {
    $output = @(& $Binary @Arguments)
    $exitCode = $LASTEXITCODE
    $line = @($output | Select-Object -First 1)
    if ($exitCode -ne 0 -or $line.Count -eq 0 -or -not $line[0]) {
        throw "$Label failed while staging Desktop resources (exit code $exitCode)"
    }
    return $line[0].TrimEnd()
}

function Assert-RegularNonReparseFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "missing $Label: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must be a regular non-reparse file: $Path"
    }
    if ($item.Length -le 0) {
        throw "$Label is empty: $Path"
    }
    return $item
}

$BinDir = [System.IO.Path]::GetFullPath($BinDir)
$NodeBin = [System.IO.Path]::GetFullPath($NodeBin)
$ContextBridgeDir = [System.IO.Path]::GetFullPath($ContextBridgeDir)
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
if (Test-Path -LiteralPath $OutputDir) {
    throw "Desktop bundle output already exists: $OutputDir"
}
if (-not (Test-Path -LiteralPath $ContextBridgeDir -PathType Container)) {
    throw "Context Bridge source directory is missing: $ContextBridgeDir"
}
$bridgeDirectoryItem = Get-Item -LiteralPath $ContextBridgeDir
if (($bridgeDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Context Bridge source must be a regular directory"
}

$runtimeDir = Join-Path $OutputDir "resources\webcodex-runtime"
$toolsDir = Join-Path $OutputDir "resources\webcodex-tools"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$shortSource = $SourceSha.Substring(0, 12).ToLowerInvariant()
$expectedMachine = if ($Platform -eq "win32-x64") { 0x8664 } else { 0xAA64 }
$binaryNames = @("webcodex", "webcodex-server", "webcodex-runner")
$resourceMap = [ordered]@{}
$fileMetadata = [ordered]@{}

try {
    foreach ($name in $binaryNames) {
        $source = Join-Path $BinDir "$name.exe"
        $sourceItem = Assert-RegularNonReparseFile $source "Desktop runtime binary"
        $line = Get-FirstOutputLine $source @("--version") "$name.exe --version"
        $expected = "$name $Version (commit $shortSource, dirty=false, built_at=$BuiltAt)"
        if ($line -ne $expected) {
            throw "unexpected $name.exe identity: '$line' (expected '$expected')"
        }
        $machine = Get-PeMachine $source
        if ($machine -ne $expectedMachine) {
            throw ("unexpected PE machine for {0}: expected 0x{1:x4}, got 0x{2:x4}" -f $source, $expectedMachine, $machine)
        }

        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $destination = Join-Path $runtimeDir "$name.exe"
        Copy-Item -LiteralPath $source -Destination $destination
        $destinationItem = Assert-RegularNonReparseFile $destination "staged Desktop runtime binary"
        $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($destinationItem.Length -ne $sourceItem.Length -or $destinationHash -ne $sourceHash) {
            throw "staged Desktop runtime byte verification failed for $name.exe"
        }

        $resourceMap[[System.IO.Path]::GetFullPath($destination)] = "webcodex-runtime/$name.exe"
        $fileMetadata[$name] = [ordered]@{
            filename = "$name.exe"
            size = [Int64]$destinationItem.Length
            source_sha256 = $sourceHash
            staged_unsigned_sha256 = $destinationHash
        }
    }

    $nodeSourceItem = Assert-RegularNonReparseFile $NodeBin "bundled Node runtime"
    $actualNodeVersion = Get-FirstOutputLine $NodeBin @("--version") "bundled Node --version"
    if ($actualNodeVersion -ne $NodeVersion) {
        throw "unexpected bundled Node version: '$actualNodeVersion' (expected '$NodeVersion')"
    }
    $nodeMachine = Get-PeMachine $NodeBin
    if ($nodeMachine -ne $expectedMachine) {
        throw ("unexpected bundled Node PE machine: expected 0x{0:x4}, got 0x{1:x4}" -f $expectedMachine, $nodeMachine)
    }
    $nodeDestinationDir = Join-Path $toolsDir "node"
    New-Item -ItemType Directory -Force -Path $nodeDestinationDir | Out-Null
    $nodeDestination = Join-Path $nodeDestinationDir "node.exe"
    Copy-Item -LiteralPath $NodeBin -Destination $nodeDestination
    $nodeDestinationItem = Assert-RegularNonReparseFile $nodeDestination "staged bundled Node runtime"
    $nodeSourceHash = (Get-FileHash -LiteralPath $NodeBin -Algorithm SHA256).Hash.ToLowerInvariant()
    $nodeHash = (Get-FileHash -LiteralPath $nodeDestination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($nodeDestinationItem.Length -ne $nodeSourceItem.Length -or $nodeHash -ne $nodeSourceHash) {
        throw "staged bundled Node byte verification failed"
    }
    $resourceMap[[System.IO.Path]::GetFullPath($nodeDestination)] = "webcodex-tools/node/node.exe"

    $bridgeDestinationDir = Join-Path $toolsDir "codex-context-bridge"
    New-Item -ItemType Directory -Force -Path $bridgeDestinationDir | Out-Null
    $bridgeMetadata = [ordered]@{}
    foreach ($name in $BridgeFiles) {
        $source = Join-Path $ContextBridgeDir $name
        $sourceItem = Assert-RegularNonReparseFile $source "Context Bridge file"
        $destination = Join-Path $bridgeDestinationDir $name
        Copy-Item -LiteralPath $source -Destination $destination
        $destinationItem = Assert-RegularNonReparseFile $destination "staged Context Bridge file"
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($destinationItem.Length -ne $sourceItem.Length -or $destinationHash -ne $sourceHash) {
            throw "staged Context Bridge byte verification failed: $name"
        }
        $resourceMap[[System.IO.Path]::GetFullPath($destination)] = "webcodex-tools/codex-context-bridge/$name"
        $bridgeMetadata[$name] = [ordered]@{
            size = [Int64]$destinationItem.Length
            sha256 = $destinationHash
        }
    }

    $packagePath = Join-Path $bridgeDestinationDir "package.json"
    $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    $bridgeVersion = [string]$package.version
    if ($bridgeVersion -ne $ContextBridgeVersion) {
        throw "unexpected Context Bridge version: '$bridgeVersion' (expected '$ContextBridgeVersion')"
    }

    $overlay = [ordered]@{
        version = $Version
        bundle = [ordered]@{
            active = $true
            targets = @("nsis")
            resources = $resourceMap
            windows = [ordered]@{
                nsis = [ordered]@{
                    installMode = "currentUser"
                }
            }
        }
    }
    $overlayPath = Join-Path $OutputDir "tauri.bundle.conf.json"
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($overlayPath, ($overlay | ConvertTo-Json -Depth 12) + "`n", $utf8)

    $metadata = [ordered]@{
        schema_version = 3
        platform = $Platform
        version = $Version
        source_sha = $SourceSha.ToLowerInvariant()
        built_at = $BuiltAt
        resource_dir = "resources/webcodex-runtime"
        tool_resource_dir = "resources/webcodex-tools"
        provenance = "same_unsigned_runtime_input_before_platform_packaging"
        files = $fileMetadata
        bundled_tools = [ordered]@{
            node = [ordered]@{
                version = $NodeVersion
                sha256 = $nodeHash
                resource = "webcodex-tools/node/node.exe"
            }
            codex_context_bridge = [ordered]@{
                version = $bridgeVersion
                resource_dir = "webcodex-tools/codex-context-bridge"
                files = $bridgeMetadata
            }
        }
    }
    $metadataPath = Join-Path $OutputDir "desktop-bundle.json"
    [System.IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json -Depth 12) + "`n", $utf8)

    Write-Output "Desktop runtime and Native Context assets staged from exact source $($SourceSha.ToLowerInvariant())"
    Write-Output "Tauri config overlay: $overlayPath"
    Write-Output "Runtime resources: $runtimeDir"
    Write-Output "Tool resources: $toolsDir"
} catch {
    if (Test-Path -LiteralPath $OutputDir) {
        Remove-Item -LiteralPath $OutputDir -Recurse -Force
    }
    throw
}
