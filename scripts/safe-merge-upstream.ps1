<#
.SYNOPSIS
Safely merge upstream changes into your current branch with backup protection.

.DESCRIPTION
This script automates a safe upstream merge workflow:
1) Validates clean git state
2) Ensures upstream remote exists (or updates URL)
3) Creates and pushes a timestamped backup branch
4) Merges upstream/<branch> into <branch>
5) Auto-resolves known generated-file conflicts by keeping local side
6) Optionally resolves all remaining conflicts by keeping local side
7) Optionally rebuilds frontend dist and commits refreshed bundles
8) Pushes <branch>

.EXAMPLE
pwsh -File .\scripts\safe-merge-upstream.ps1

.EXAMPLE
pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -PreferOursOnRemainingConflicts

.EXAMPLE
pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -BuildFrontendDist
#>

[CmdletBinding()]
param(
    [string]$Branch = "main",
    [string]$UpstreamRemote = "upstream",
    [string]$UpstreamUrl = "https://github.com/marketcalls/openalgo.git",
    [string]$OriginRemote = "origin",
    [switch]$NoPush,
    [switch]$PreferOursOnRemainingConflicts,
    [switch]$BuildFrontendDist
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Invoke-GitCapture {
    param(
        [string[]]$GitArgs,
        [switch]$AllowFailure
    )

    Write-Host ">> git $($GitArgs -join ' ')" -ForegroundColor DarkGray
    $output = & git @GitArgs 2>&1
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        $text = ($output | Out-String).Trim()
        throw "Git command failed ($exitCode): git $($GitArgs -join ' ')`n$text"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
    }
}

function Invoke-Git {
    param([string[]]$GitArgs)
    $result = Invoke-GitCapture -GitArgs $GitArgs
    return $result
}

function Get-Text([object]$value) {
    return (($value | Out-String).Trim())
}

function Test-PathMatchAny {
    param(
        [string]$Path,
        [string[]]$Patterns
    )
    foreach ($pattern in $Patterns) {
        if ($Path -like $pattern) {
            return $true
        }
    }
    return $false
}

function Resolve-WithOurs {
    param([string]$File)
    & git checkout --ours -- "$File" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    & git add -- "$File"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stage $File after taking ours"
    }
    return $true
}

function Resolve-ByRemoving {
    param([string]$File)
    & git rm --quiet -- "$File" 2>$null
    if ($LASTEXITCODE -eq 0) {
        return $true
    }
    & git rm --quiet -f -- "$File" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Build-FrontendDist {
    Write-Step "Building frontend dist (npm run build)"

    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        throw "npm is not available on PATH. Install Node.js/npm or run without -BuildFrontendDist."
    }

    $frontendDir = Join-Path (Get-Location) "frontend"
    if (-not (Test-Path $frontendDir)) {
        throw "frontend directory not found at: $frontendDir"
    }

    Push-Location $frontendDir
    try {
        Write-Host ">> npm run build" -ForegroundColor DarkGray
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    Invoke-Git -GitArgs @("add", "-A", "frontend/dist") | Out-Null

    $distChanges = Get-Text((Invoke-Git -GitArgs @("status", "--porcelain", "--", "frontend/dist")).Output)
    if ($distChanges) {
        Invoke-Git -GitArgs @("commit", "-m", "chore(frontend): refresh dist after upstream merge") | Out-Null
        Write-Host "Committed refreshed frontend/dist bundles." -ForegroundColor Green
    } else {
        Write-Host "No frontend/dist changes after build." -ForegroundColor Gray
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Safe Upstream Merge" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Step "Validating repository state"
$inside = Get-Text((Invoke-Git -GitArgs @("rev-parse", "--is-inside-work-tree")).Output)
if ($inside -ne "true") {
    throw "Current directory is not a git repository."
}

$gitDir = Get-Text((Invoke-Git -GitArgs @("rev-parse", "--git-dir")).Output)
if (Test-Path (Join-Path $gitDir "MERGE_HEAD")) {
    throw "A merge is already in progress. Resolve it before running this script."
}
if ((Test-Path (Join-Path $gitDir "rebase-merge")) -or (Test-Path (Join-Path $gitDir "rebase-apply"))) {
    throw "A rebase is in progress. Resolve/abort it before running this script."
}

$currentBranch = Get-Text((Invoke-Git -GitArgs @("rev-parse", "--abbrev-ref", "HEAD")).Output)
if ($currentBranch -ne $Branch) {
    throw "Current branch is '$currentBranch'. Checkout '$Branch' before running."
}

$dirty = Get-Text((Invoke-Git -GitArgs @("status", "--porcelain")).Output)
if ($dirty) {
    throw "Working tree is not clean. Commit/stash changes first."
}

Write-Step "Ensuring remotes"
$remoteCheck = Invoke-GitCapture -GitArgs @("remote", "get-url", $UpstreamRemote) -AllowFailure
if ($remoteCheck.ExitCode -ne 0) {
    Invoke-Git -GitArgs @("remote", "add", $UpstreamRemote, $UpstreamUrl) | Out-Null
} else {
    $existingUrl = Get-Text($remoteCheck.Output)
    if ($existingUrl -ne $UpstreamUrl) {
        Invoke-Git -GitArgs @("remote", "set-url", $UpstreamRemote, $UpstreamUrl) | Out-Null
    }
}
Invoke-Git -GitArgs @("fetch", $OriginRemote, "--prune") | Out-Null
Invoke-Git -GitArgs @("fetch", $UpstreamRemote, "--prune") | Out-Null

Write-Step "Creating backup branch"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupBranch = "backup/$Branch-pre-upstream-$timestamp"
Invoke-Git -GitArgs @("branch", $backupBranch) | Out-Null
Invoke-Git -GitArgs @("push", "-u", $OriginRemote, $backupBranch) | Out-Null
Write-Host "Backup branch pushed: $backupBranch" -ForegroundColor Green

Write-Step "Merging $UpstreamRemote/$Branch into $Branch"
$mergeRef = "refs/remotes/$UpstreamRemote/$Branch"
$mergeResult = Invoke-GitCapture -GitArgs @("merge", "--no-ff", "--no-edit", $mergeRef) -AllowFailure

if ($mergeResult.ExitCode -ne 0) {
    $mergeInProgress = Invoke-GitCapture -GitArgs @("rev-parse", "-q", "--verify", "MERGE_HEAD") -AllowFailure
    if ($mergeInProgress.ExitCode -ne 0) {
        $msg = Get-Text($mergeResult.Output)
        throw "Merge failed before conflict handling.`n$msg"
    }

    Write-Step "Auto-resolving merge conflicts"
    $conflictOutput = Invoke-GitCapture -GitArgs @("diff", "--name-only", "--diff-filter=U")
    $conflicts = @(
        $conflictOutput.Output |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ }
    )

    if ($conflicts.Count -eq 0) {
        throw "Merge reported failure but no conflicted files were detected."
    }

    $oursPreferPatterns = @(
        "frontend/dist/*",
        "frontend\dist\*",
        "static/css/main.css"
    )

    $resolved = New-Object System.Collections.Generic.List[string]
    $unresolved = New-Object System.Collections.Generic.List[string]

    foreach ($file in $conflicts) {
        $resolvedThis = $false

        if (Test-PathMatchAny -Path $file -Patterns $oursPreferPatterns) {
            $resolvedThis = Resolve-WithOurs -File $file
            if (-not $resolvedThis) {
                # Handles rename/delete cases where ours side does not exist.
                $resolvedThis = Resolve-ByRemoving -File $file
            }
        } elseif ($PreferOursOnRemainingConflicts) {
            $resolvedThis = Resolve-WithOurs -File $file
            if (-not $resolvedThis) {
                $resolvedThis = Resolve-ByRemoving -File $file
            }
        }

        if ($resolvedThis) {
            $resolved.Add($file)
        } else {
            $unresolved.Add($file)
        }
    }

    $remaining = @(
        (Invoke-GitCapture -GitArgs @("diff", "--name-only", "--diff-filter=U")).Output |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ }
    )

    if ($remaining.Count -gt 0) {
        Write-Host ""
        Write-Host "Unresolved conflicts remain:" -ForegroundColor Yellow
        $remaining | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
        throw "Stopping with merge in progress. Resolve files above, then run 'git commit' to finish merge."
    }

    Write-Host "Auto-resolved $($resolved.Count) conflicted file(s)." -ForegroundColor Green

    # Merge commit may not be created yet after manual conflict resolutions.
    Invoke-Git -GitArgs @("commit", "--no-edit") | Out-Null
}

if ($BuildFrontendDist) {
    Build-FrontendDist
}

if (-not $NoPush) {
    Write-Step "Pushing merged branch to $OriginRemote/$Branch"
    Invoke-Git -GitArgs @("push", $OriginRemote, $Branch) | Out-Null
}

$finalStatus = Get-Text((Invoke-Git -GitArgs @("status", "--short", "--branch")).Output)

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Merge Completed Successfully" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Backup branch : $backupBranch" -ForegroundColor Gray
if ($NoPush) {
    Write-Host "Push skipped   : run 'git push $OriginRemote $Branch' when ready" -ForegroundColor Gray
}
Write-Host ""
Write-Host $finalStatus

