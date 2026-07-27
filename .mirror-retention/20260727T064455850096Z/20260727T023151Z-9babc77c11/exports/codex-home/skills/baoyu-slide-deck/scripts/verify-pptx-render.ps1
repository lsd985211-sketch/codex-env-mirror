[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PresentationPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 500)]
    [int]$ExpectedSlides,

    [ValidateRange(320, 7680)]
    [int]$Width = 1600,

    [ValidateRange(180, 4320)]
    [int]$Height = 900,

    [switch]$ReplaceOutput
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$markerName = '.baoyu-slide-render-output'
$receiptName = 'validation-ppt-render.json'
$powerPoint = $null
$presentation = $null

function Release-ComObject {
    param([object]$Value)
    if ($null -ne $Value) {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($Value)
    }
}

try {
    $source = (Resolve-Path -LiteralPath $PresentationPath).Path
    $output = [IO.Path]::GetFullPath($OutputDirectory)

    if (Test-Path -LiteralPath $output) {
        $items = @(Get-ChildItem -LiteralPath $output -Force)
        $marker = Join-Path $output $markerName
        if ($items.Count -gt 0 -and -not $ReplaceOutput) {
            throw "Output directory is not empty. Choose a new directory or pass -ReplaceOutput."
        }
        if ($items.Count -gt 0 -and -not (Test-Path -LiteralPath $marker)) {
            throw "Refusing to replace a nonempty directory not owned by this validator: $output"
        }
        if ($ReplaceOutput -and (Test-Path -LiteralPath $marker)) {
            Remove-Item -LiteralPath $output -Recurse -Force
        }
    }

    New-Item -ItemType Directory -Path $output -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $output $markerName) -Value 'baoyu-slide-deck render output' -Encoding ASCII

    $powerPoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerPoint.Presentations.Open($source, $true, $true, $false)
    $actualSlides = [int]$presentation.Slides.Count
    if ($actualSlides -ne $ExpectedSlides) {
        throw "PowerPoint slide count mismatch: expected $ExpectedSlides, got $actualSlides"
    }

    $presentation.Export($output, 'PNG', $Width, $Height)
    $images = @(Get-ChildItem -LiteralPath $output -File | Where-Object { $_.Extension -match '^\.(png|jpg|jpeg)$' })
    if ($images.Count -ne $ExpectedSlides) {
        throw "PowerPoint export count mismatch: expected $ExpectedSlides, got $($images.Count)"
    }

    $receipt = [ordered]@{
        schema = 'baoyu-slide-deck.powerpoint-render.v1'
        ok = $true
        presentation_path = $source
        output_directory = $output
        expected_slides = $ExpectedSlides
        rendered_slides = $images.Count
        width = $Width
        height = $Height
        power_point_version = [string]$powerPoint.Version
        with_window = $false
    }
    $receiptPath = Join-Path $output $receiptName
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    $receipt | ConvertTo-Json -Compress
}
catch {
    $failure = [ordered]@{
        schema = 'baoyu-slide-deck.powerpoint-render.v1'
        ok = $false
        presentation_path = $PresentationPath
        output_directory = $OutputDirectory
        expected_slides = $ExpectedSlides
        error = $_.Exception.Message
    }
    $failure | ConvertTo-Json -Compress
    exit 1
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        Release-ComObject $presentation
    }
    if ($null -ne $powerPoint) {
        $powerPoint.Quit()
        Release-ComObject $powerPoint
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
