$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$GenerateIconScript = Join-Path $RepoRoot "scripts\generate_icon.py"
$IconPath = Join-Path $RepoRoot "auroramic.ico"
$LauncherSource = Join-Path $RepoRoot "launcher.cs"
$OutputExe = Join-Path $RepoRoot "AuroraMic.exe"

function Get-PythonVersionKey {
    param([string]$FolderName)
    $suffix = $FolderName -replace '^Python', '' -replace '-.*$', ''
    if ($suffix -match '^\d+\.\d+') {
        return [version]$Matches[0]
    }
    if ($suffix -match '^(\d)(\d+)$') {
        return [version]("{0}.{1}" -f $Matches[1], $Matches[2])
    }
    return [version]"0.0"
}

function Resolve-PythonCommand {
    $candidates = New-Object System.Collections.Generic.List[hashtable]
    $seen = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)

    function Add-Candidate {
        param([string]$FilePath, [string[]]$PrefixArgs)
        if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path $FilePath)) {
            return
        }
        $full = [System.IO.Path]::GetFullPath($FilePath)
        $sig = $full + "|" + ($PrefixArgs -join " ")
        if ($seen.Add($sig)) {
            $candidates.Add(@{ FilePath = $full; PrefixArgs = $PrefixArgs })
        }
    }

    Add-Candidate (Join-Path $RepoRoot ".venv\Scripts\python.exe") @()
    Add-Candidate (Join-Path $RepoRoot "venv\Scripts\python.exe") @()

    foreach ($cmd in @(Get-Command "python.exe" -All -ErrorAction SilentlyContinue)) {
        Add-Candidate $cmd.Source @()
    }

    $localPyBase = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $localPyBase) {
        $localPythons = Get-ChildItem -Path $localPyBase -Directory -Filter "Python*" |
            Sort-Object { Get-PythonVersionKey $_.Name } -Descending
        foreach ($dir in $localPythons) {
            Add-Candidate (Join-Path $dir.FullName "python.exe") @()
        }
    }

    $pyCmd = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pyCmd) {
        Add-Candidate $pyCmd.Source @("-3")
    }

    if ($candidates.Count -eq 0) {
        throw "Could not find a Python 3 interpreter (.venv, PATH, %LOCALAPPDATA%\Programs\Python, or py.exe)."
    }

    foreach ($cand in $candidates) {
        $probeArgs = @($cand.PrefixArgs) + @("-c", "import PIL, numpy")
        try {
            $null = & $cand.FilePath @probeArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $cand
            }
        }
        catch {
            # Try next Python candidate.
        }
    }

    throw "Found Python interpreter(s), but none can import 'PIL' and 'numpy'. Run 'pip install -r requirements.txt' first."
}

function Resolve-CscCompiler {
    $candidates = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    foreach ($fwRoot in @("Framework64", "Framework")) {
        $rootPath = Join-Path $env:WINDIR ("Microsoft.NET\" + $fwRoot)
        if (Test-Path $rootPath) {
            $found = Get-ChildItem -Path $rootPath -Directory -Filter "v4*" |
                Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName "csc.exe" } |
                Where-Object { Test-Path $_ } |
                Select-Object -First 1
            if ($null -ne $found) {
                return $found
            }
        }
    }

    throw "Could not locate .NET Framework 4.x csc.exe."
}

if (-not (Test-Path $GenerateIconScript)) {
    throw "Missing icon generator script: $GenerateIconScript"
}
if (-not (Test-Path $LauncherSource)) {
    throw "Missing launcher source file: $LauncherSource"
}

$py = Resolve-PythonCommand
Write-Host ("Generating icon via " + $py.FilePath + "...")
$pyArgs = @($py.PrefixArgs) + @($GenerateIconScript)
& $py.FilePath @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw ("Icon generation failed with exit code " + $LASTEXITCODE)
}
if (-not (Test-Path $IconPath)) {
    throw "Icon generation succeeded but auroramic.ico was not created."
}

$csc = Resolve-CscCompiler
Write-Host ("Compiling AuroraMic.exe via " + $csc + "...")
& $csc /nologo /target:winexe /optimize+ /platform:anycpu "/win32icon:$IconPath" "/out:$OutputExe" "$LauncherSource"
if ($LASTEXITCODE -ne 0) {
    throw ("csc.exe compilation failed with exit code " + $LASTEXITCODE)
}
if (-not (Test-Path $OutputExe)) {
    throw "Compilation succeeded but AuroraMic.exe was not found."
}

$exeInfo = Get-Item $OutputExe
Write-Host ("Built " + $exeInfo.FullName + " (" + $exeInfo.Length + " bytes)")
