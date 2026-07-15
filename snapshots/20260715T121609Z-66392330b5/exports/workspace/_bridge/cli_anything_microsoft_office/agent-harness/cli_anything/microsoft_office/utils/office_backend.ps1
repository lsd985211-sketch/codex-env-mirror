param(
    [Parameter(Mandatory = $true)]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Read-Payload {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { return [pscustomobject]@{} }
    return $raw | ConvertFrom-Json
}

function Write-Result([object]$Value) {
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 12 -Compress))
}

function Release-Com([object]$Value) {
    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) } catch {}
    }
}

function Full-Path([string]$Value) {
    return [System.IO.Path]::GetFullPath($Value)
}

function Set-Excel-CellValue([object]$Cell, [object]$Value) {
    # PowerShell's COM binder can choose a string-only Range setter for JSON
    # Int32/Double values. Normalize scalar types before crossing the COM
    # boundary while preserving the value class that Excel can represent.
    if ($null -eq $Value) {
        $Cell.Value2 = $null
    } elseif ($Value -is [bool]) {
        $Cell.Value2 = [bool]$Value
    } elseif ($Value -is [byte] -or $Value -is [int16] -or $Value -is [int32] -or $Value -is [int64] -or $Value -is [single] -or $Value -is [double] -or $Value -is [decimal]) {
        $Cell.Value2 = [double]$Value
    } else {
        $Cell.Value2 = [string]$Value
    }
}

function Safe-App([object]$App) {
    try { $App.Visible = $false } catch {}
    try { $App.DisplayAlerts = 0 } catch {}
    try { $App.AutomationSecurity = 3 } catch {}
}

function File-Result([string]$ActionName, [string]$Path, [hashtable]$Extra = @{}) {
    $item = Get-Item -LiteralPath $Path
    $result = [ordered]@{
        ok = $true
        action = $ActionName
        path = $item.FullName
        size = $item.Length
        modified_at = $item.LastWriteTimeUtc.ToString('o')
    }
    foreach ($key in $Extra.Keys) { $result[$key] = $Extra[$key] }
    return $result
}

. (Join-Path $PSScriptRoot 'office_edit_backend.ps1')

$payload = Read-Payload

try {
    switch ($Action) {
        'system.status' {
            $definitions = @(
                @{ name = 'word'; prog_id = 'Word.Application'; exe = 'WINWORD.EXE' },
                @{ name = 'excel'; prog_id = 'Excel.Application'; exe = 'EXCEL.EXE' },
                @{ name = 'powerpoint'; prog_id = 'PowerPoint.Application'; exe = 'POWERPNT.EXE' }
            )
            $apps = @()
            foreach ($definition in $definitions) {
                $appPathKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$($definition.exe)"
                $comKey = "Registry::HKEY_CLASSES_ROOT\$($definition.prog_id)\CLSID"
                $exePath = ''
                if (Test-Path -LiteralPath $appPathKey) {
                    $exePath = (Get-ItemProperty -LiteralPath $appPathKey).'(default)'
                }
                $apps += [ordered]@{
                    name = $definition.name
                    prog_id = $definition.prog_id
                    com_registered = Test-Path -LiteralPath $comKey
                    executable = $exePath
                    executable_exists = (-not [string]::IsNullOrWhiteSpace($exePath)) -and (Test-Path -LiteralPath $exePath)
                }
            }
            $configuration = $null
            $ctr = 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration'
            if (Test-Path -LiteralPath $ctr) {
                $value = Get-ItemProperty -LiteralPath $ctr
                $configuration = [ordered]@{
                    product_ids = $value.ProductReleaseIds
                    version = $value.VersionToReport
                    platform = $value.Platform
                }
            }
            Write-Result ([ordered]@{ ok = $true; action = $Action; apps = $apps; click_to_run = $configuration })
        }
        'word.create' {
            $app = $null; $doc = $null
            try {
                $output = Full-Path $payload.output
                $app = New-Object -ComObject Word.Application
                Safe-App $app
                $doc = $app.Documents.Add()
                $parts = @()
                if (-not [string]::IsNullOrWhiteSpace([string]$payload.title)) { $parts += [string]$payload.title }
                if (-not [string]::IsNullOrWhiteSpace([string]$payload.body)) { $parts += [string]$payload.body }
                $doc.Content.Text = ($parts -join "`r`n`r`n")
                $doc.SaveAs2($output, 16)
                Write-Result (File-Result $Action $output @{ application = 'word' })
            } finally {
                if ($null -ne $doc) { try { $doc.Close(0) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $doc; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'word.inspect' { Write-Result (Inspect-Word $payload) }
        'word.edit' { Write-Result (Invoke-WordEdit $payload) }
        'word.info' {
            $app = $null; $doc = $null
            try {
                $path = Full-Path $payload.path
                $app = New-Object -ComObject Word.Application
                Safe-App $app
                $doc = $app.Documents.Open($path, $false, $true)
                $result = [ordered]@{
                    ok = $true; action = $Action; application = 'word'; path = $path
                    pages = $doc.ComputeStatistics(2)
                    paragraphs = $doc.Paragraphs.Count
                    tables = $doc.Tables.Count
                    words = $doc.Words.Count
                    characters = $doc.Characters.Count
                }
                Write-Result $result
            } finally {
                if ($null -ne $doc) { try { $doc.Close(0) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $doc; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'word.export_pdf' {
            $app = $null; $doc = $null
            try {
                $path = Full-Path $payload.path; $output = Full-Path $payload.output
                $app = New-Object -ComObject Word.Application
                Safe-App $app
                $doc = $app.Documents.Open($path, $false, $true)
                $doc.ExportAsFixedFormat($output, 17)
                Write-Result (File-Result $Action $output @{ application = 'word'; source = $path })
            } finally {
                if ($null -ne $doc) { try { $doc.Close(0) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $doc; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'excel.create' {
            $app = $null; $workbook = $null; $sheet = $null
            try {
                $output = Full-Path $payload.output
                $app = New-Object -ComObject Excel.Application
                Safe-App $app
                try { $app.AskToUpdateLinks = $false } catch {}
                $workbook = $app.Workbooks.Add()
                $sheet = $workbook.Worksheets.Item(1)
                if (-not [string]::IsNullOrWhiteSpace([string]$payload.sheet)) { $sheet.Name = [string]$payload.sheet }
                $rowIndex = 1
                foreach ($row in @($payload.rows)) {
                    $columnIndex = 1
                foreach ($value in @($row)) {
                    $cell = $sheet.Cells.Item($rowIndex, $columnIndex)
                    Set-Excel-CellValue $cell $value
                    Release-Com $cell
                    $columnIndex++
                }
                    $rowIndex++
                }
                $workbook.SaveAs($output, 51)
                Write-Result (File-Result $Action $output @{ application = 'excel'; rows = [Math]::Max(0, $rowIndex - 1) })
            } finally {
                if ($null -ne $workbook) { try { $workbook.Close($false) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $sheet; Release-Com $workbook; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'excel.inspect' { Write-Result (Inspect-Excel $payload) }
        'excel.edit' { Write-Result (Invoke-ExcelEdit $payload) }
        'excel.info' {
            $app = $null; $workbook = $null
            try {
                $path = Full-Path $payload.path
                $app = New-Object -ComObject Excel.Application
                Safe-App $app
                try { $app.AskToUpdateLinks = $false } catch {}
                $workbook = $app.Workbooks.Open($path, 0, $true)
                $sheets = @()
                foreach ($sheet in @($workbook.Worksheets)) {
                    $used = $sheet.UsedRange
                    $sheets += [ordered]@{ name = $sheet.Name; rows = $used.Rows.Count; columns = $used.Columns.Count }
                    Release-Com $used; Release-Com $sheet
                }
                Write-Result ([ordered]@{ ok = $true; action = $Action; application = 'excel'; path = $path; worksheets = $sheets })
            } finally {
                if ($null -ne $workbook) { try { $workbook.Close($false) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $workbook; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'excel.export_pdf' {
            $app = $null; $workbook = $null
            try {
                $path = Full-Path $payload.path; $output = Full-Path $payload.output
                $app = New-Object -ComObject Excel.Application
                Safe-App $app
                try { $app.AskToUpdateLinks = $false } catch {}
                $workbook = $app.Workbooks.Open($path, 0, $true)
                $workbook.ExportAsFixedFormat(0, $output)
                Write-Result (File-Result $Action $output @{ application = 'excel'; source = $path })
            } finally {
                if ($null -ne $workbook) { try { $workbook.Close($false) } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $workbook; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'powerpoint.create' {
            $app = $null; $presentation = $null; $slide = $null
            try {
                $output = Full-Path $payload.output
                $app = New-Object -ComObject PowerPoint.Application
                Safe-App $app
                $presentation = $app.Presentations.Add()
                $slide = $presentation.Slides.Add(1, 1)
                $slide.Shapes.Title.TextFrame.TextRange.Text = [string]$payload.title
                try { $slide.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = [string]$payload.subtitle } catch {}
                $presentation.SaveAs($output, 24)
                Write-Result (File-Result $Action $output @{ application = 'powerpoint'; slides = 1 })
            } finally {
                if ($null -ne $presentation) { try { $presentation.Close() } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $slide; Release-Com $presentation; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'powerpoint.inspect' { Write-Result (Inspect-PowerPoint $payload) }
        'powerpoint.edit' { Write-Result (Invoke-PowerPointEdit $payload) }
        'powerpoint.info' {
            $app = $null; $presentation = $null
            try {
                $path = Full-Path $payload.path
                $app = New-Object -ComObject PowerPoint.Application
                Safe-App $app
                $presentation = $app.Presentations.Open($path, -1, 0, 0)
                $titles = @()
                foreach ($slide in @($presentation.Slides)) {
                    $text = ''
                    try { if ($slide.Shapes.HasTitle) { $text = $slide.Shapes.Title.TextFrame.TextRange.Text } } catch {}
                    $titles += $text
                    Release-Com $slide
                }
                Write-Result ([ordered]@{ ok = $true; action = $Action; application = 'powerpoint'; path = $path; slides = $presentation.Slides.Count; titles = $titles })
            } finally {
                if ($null -ne $presentation) { try { $presentation.Close() } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $presentation; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        'powerpoint.export_pdf' {
            $app = $null; $presentation = $null
            try {
                $path = Full-Path $payload.path; $output = Full-Path $payload.output
                $app = New-Object -ComObject PowerPoint.Application
                Safe-App $app
                $presentation = $app.Presentations.Open($path, -1, 0, 0)
                $presentation.SaveAs($output, 32)
                Write-Result (File-Result $Action $output @{ application = 'powerpoint'; source = $path })
            } finally {
                if ($null -ne $presentation) { try { $presentation.Close() } catch {} }
                if ($null -ne $app) { try { $app.Quit() } catch {} }
                Release-Com $presentation; Release-Com $app
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            }
        }
        default { throw "Unsupported backend action: $Action" }
    }
} catch {
    Write-Result ([ordered]@{
        ok = $false
        action = $Action
        error = $_.Exception.Message
        error_type = $_.Exception.GetType().FullName
        hresult = $_.Exception.HResult
    })
    exit 1
}
