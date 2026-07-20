function Has-Field([object]$Value, [string]$Name) {
    return $null -ne $Value -and $Value.PSObject.Properties.Name -contains $Name
}

function Office-Color([string]$Value) {
    $text = $Value.Trim().TrimStart('#')
    if ($text -notmatch '^[0-9A-Fa-f]{6}$') { throw "Color must be #RRGGBB: $Value" }
    $r = [Convert]::ToInt32($text.Substring(0, 2), 16)
    $g = [Convert]::ToInt32($text.Substring(2, 2), 16)
    $b = [Convert]::ToInt32($text.Substring(4, 2), 16)
    return $r + (256 * $g) + (65536 * $b)
}

function Add-Receipt([System.Collections.ArrayList]$Receipts, [int]$Index, [object]$Operation, [hashtable]$Extra = @{}) {
    $item = [ordered]@{ index = $Index; operation = [string]$Operation.op; status = 'completed' }
    foreach ($key in $Extra.Keys) { $item[$key] = $Extra[$key] }
    [void]$Receipts.Add($item)
}

function Promote-OfficeOutput([string]$Temporary, [string]$Output) {
    if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }
    Move-Item -LiteralPath $Temporary -Destination $Output -Force
}

function Set-BuiltinProperty([object]$Container, [string]$Name, [object]$Value) {
    $properties = $null; $property = $null
    try {
        $properties = $Container.BuiltInDocumentProperties
        $property = $properties.Item($Name)
        $property.Value = $Value
    } finally {
        Release-Com $property; Release-Com $properties
    }
}

function Inspect-Word([object]$Payload) {
    $app = $null; $doc = $null
    try {
        $path = Full-Path $Payload.path
        $app = New-Object -ComObject Word.Application; Safe-App $app
        $doc = $app.Documents.Open($path, $false, $true)
        $paragraphs = @(); $limit = [Math]::Min(100, $doc.Paragraphs.Count)
        for ($i = 1; $i -le $limit; $i++) {
            $paragraph = $null; $range = $null
            try {
                $paragraph = $doc.Paragraphs.Item($i); $range = $paragraph.Range
                $text = ([string]$range.Text).TrimEnd("`r", "`a")
                $style = $range.Style
                try { $styleName = [string]$style.NameLocal } catch { $styleName = [string]$style }
                $paragraphs += [ordered]@{ index = $i; text = $text.Substring(0, [Math]::Min(500, $text.Length)); style = $styleName }
                Release-Com $style
            } finally { Release-Com $range; Release-Com $paragraph }
        }
        $tables = @()
        for ($i = 1; $i -le [Math]::Min(50, $doc.Tables.Count); $i++) {
            $table = $null
            try { $table = $doc.Tables.Item($i); $tables += [ordered]@{ index = $i; rows = $table.Rows.Count; columns = $table.Columns.Count } }
            finally { Release-Com $table }
        }
        return [ordered]@{ ok = $true; action = 'word.inspect'; application = 'word'; path = $path; pages = $doc.ComputeStatistics(2); sections = $doc.Sections.Count; paragraphs = $paragraphs; tables = $tables; truncated = ($doc.Paragraphs.Count -gt $limit) }
    } finally {
        if ($null -ne $doc) { try { $doc.Close(0) } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $doc; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-WordEdit([object]$Payload) {
    $app = $null; $doc = $null; $success = $false
    $source = Full-Path $Payload.path; $temporary = Full-Path $Payload.temporary; $output = Full-Path $Payload.output
    Copy-Item -LiteralPath $source -Destination $temporary -Force
    $receipts = [System.Collections.ArrayList]::new()
    try {
        $app = New-Object -ComObject Word.Application; Safe-App $app
        $doc = $app.Documents.Open($temporary, $false, $false)
        $index = 0
        foreach ($op in @($Payload.operations)) {
            switch ([string]$op.op) {
                'replace_text' {
                    $range = $doc.Content; $find = $range.Find
                    try {
                        $matchCase = (Has-Field $op 'match_case') -and [bool]$op.match_case
                        $wholeWord = (Has-Field $op 'whole_word') -and [bool]$op.whole_word
                        [void]$find.Execute([string]$op.find, $matchCase, $wholeWord, $false, $false, $false, $true, 1, $false, [string]$op.replace, 2)
                    } finally { Release-Com $find; Release-Com $range }
                }
                'delete_text' {
                    $range = $doc.Content; $find = $range.Find
                    try { [void]$find.Execute([string]$op.find, [bool]$op.match_case, [bool]$op.whole_word, $false, $false, $false, $true, 1, $false, '', 2) }
                    finally { Release-Com $find; Release-Com $range }
                }
                'append_text' { $range = $doc.Content; try { $range.Collapse(0); $range.InsertAfter([string]$op.text) } finally { Release-Com $range } }
                'insert_paragraph' {
                    if (Has-Field $op 'index') { $paragraph = $doc.Paragraphs.Item([int]$op.index); try { $paragraph.Range.InsertBefore(([string]$op.text) + "`r") } finally { Release-Com $paragraph } }
                    else { $range = $doc.Content; try { $range.Collapse(0); $range.InsertAfter(([string]$op.text) + "`r") } finally { Release-Com $range } }
                }
                'add_heading' {
                    $level = if (Has-Field $op 'level') { [Math]::Min(9, [int]$op.level) } else { 1 }
                    $text = [string]$op.text; $endPosition = [Math]::Max(0, $doc.Content.End - 1)
                    $anchor = $doc.Range($endPosition, $endPosition)
                    try { $anchor.InsertBefore("`r" + $text) } finally { Release-Com $anchor }
                    $range = $doc.Range($endPosition + 1, $endPosition + 1 + $text.Length)
                    try { $range.Style = -($level + 1) } finally { Release-Com $range }
                }
                'add_page_break' {
                    $range = if (Has-Field $op 'index') { $doc.Paragraphs.Item([int]$op.index).Range } else { $doc.Content }
                    try { if (-not (Has-Field $op 'index')) { $range.Collapse(0) }; $range.InsertBreak(7) } finally { Release-Com $range }
                }
                'add_table' {
                    $rows = @($op.rows); $columnCount = 1
                    foreach ($row in $rows) { $columnCount = [Math]::Max($columnCount, @($row).Count) }
                    $range = if (Has-Field $op 'index') { $doc.Paragraphs.Item([int]$op.index).Range } else { $doc.Content }
                    if (-not (Has-Field $op 'index')) { $range.Collapse(0) }
                    $table = $doc.Tables.Add($range, [Math]::Max(1, $rows.Count), $columnCount)
                    try {
                        for ($r = 0; $r -lt $rows.Count; $r++) { for ($c = 0; $c -lt @($rows[$r]).Count; $c++) { $cell = $table.Cell($r + 1, $c + 1); try { $cell.Range.Text = [string]$rows[$r][$c] } finally { Release-Com $cell } } }
                        if (Has-Field $op 'style') { $table.Style = [string]$op.style }
                    } finally { Release-Com $table; Release-Com $range }
                }
                'format_text' {
                    $search = $doc.Content.Duplicate
                    try {
                        while ($search.Find.Execute([string]$op.find, [bool]$op.match_case, $false, $false, $false, $false, $true, 0, $false)) {
                            if (Has-Field $op 'bold') { $search.Font.Bold = if ([bool]$op.bold) { -1 } else { 0 } }
                            if (Has-Field $op 'italic') { $search.Font.Italic = if ([bool]$op.italic) { -1 } else { 0 } }
                            if (Has-Field $op 'underline') { $search.Font.Underline = if ([bool]$op.underline) { 1 } else { 0 } }
                            if (Has-Field $op 'font_size') { $search.Font.Size = [double]$op.font_size }
                            if (Has-Field $op 'font_name') { $search.Font.Name = [string]$op.font_name }
                            if (Has-Field $op 'color') { $search.Font.Color = Office-Color ([string]$op.color) }
                            $search.Start = $search.End; $search.End = $doc.Content.End
                        }
                    } finally { Release-Com $search }
                }
                'set_paragraph_format' {
                    $paragraph = $doc.Paragraphs.Item([int]$op.index)
                    try {
                        $format = $paragraph.Format
                        if (Has-Field $op 'alignment') { $format.Alignment = @{ left = 0; center = 1; right = 2; justify = 3 }[[string]$op.alignment] }
                        if (Has-Field $op 'space_before') { $format.SpaceBefore = [double]$op.space_before }
                        if (Has-Field $op 'space_after') { $format.SpaceAfter = [double]$op.space_after }
                        if (Has-Field $op 'line_spacing') { $format.LineSpacing = [double]$op.line_spacing }
                        Release-Com $format
                    } finally { Release-Com $paragraph }
                }
                'set_page_setup' {
                    $setup = $doc.PageSetup
                    try {
                        $marginProperties = @{ top_margin = 'TopMargin'; bottom_margin = 'BottomMargin'; left_margin = 'LeftMargin'; right_margin = 'RightMargin' }
                        foreach ($field in $marginProperties.Keys) { if (Has-Field $op $field) { $name = $marginProperties[$field]; $setup.$name = [double]$op.$field } }
                        if (Has-Field $op 'orientation') { $setup.Orientation = if ([string]$op.orientation -eq 'landscape') { 1 } else { 0 } }
                    } finally { Release-Com $setup }
                }
                'set_header' { $section = $doc.Sections.Item($(if (Has-Field $op 'section') { [int]$op.section } else { 1 })); try { $header = $section.Headers.Item(1); try { $header.Range.Text = [string]$op.text } finally { Release-Com $header } } finally { Release-Com $section } }
                'set_footer' { $section = $doc.Sections.Item($(if (Has-Field $op 'section') { [int]$op.section } else { 1 })); try { $footer = $section.Footers.Item(1); try { $footer.Range.Text = [string]$op.text } finally { Release-Com $footer } } finally { Release-Com $section } }
                'set_property' { Set-BuiltinProperty $doc ([string]$op.name) $op.value }
                default { throw "Unsupported word operation: $($op.op)" }
            }
            Add-Receipt $receipts $index $op; $index++
        }
        $doc.Save(); $success = $true
    } finally {
        if ($null -ne $doc) { try { $doc.Close($(if ($success) { -1 } else { 0 })) } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $doc; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        if (-not $success -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
    Promote-OfficeOutput $temporary $output
    return File-Result 'word.edit' $output @{ application = 'word'; source = $source; operation_count = $receipts.Count; receipts = @($receipts) }
}

function Inspect-Excel([object]$Payload) {
    $app = $null; $workbook = $null
    try {
        $path = Full-Path $Payload.path; $app = New-Object -ComObject Excel.Application; Safe-App $app; $app.AskToUpdateLinks = $false
        $workbook = $app.Workbooks.Open($path, 0, $true); $sheets = @()
        foreach ($sheet in @($workbook.Worksheets)) {
            $used = $sheet.UsedRange
            try {
                $sample = @(); $rowLimit = [Math]::Min(10, $used.Rows.Count); $columnLimit = [Math]::Min(10, $used.Columns.Count)
                for ($r = 1; $r -le $rowLimit; $r++) { $row = @(); for ($c = 1; $c -le $columnLimit; $c++) { $cell = $used.Cells.Item($r, $c); try { $row += $cell.Value2 } finally { Release-Com $cell } }; $sample += ,$row }
                $sheets += [ordered]@{ name = $sheet.Name; used_address = $used.Address(); rows = $used.Rows.Count; columns = $used.Columns.Count; sample = $sample }
            } finally { Release-Com $used; Release-Com $sheet }
        }
        return [ordered]@{ ok = $true; action = 'excel.inspect'; application = 'excel'; path = $path; worksheets = $sheets }
    } finally {
        if ($null -ne $workbook) { try { $workbook.Close($false) } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $workbook; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-ExcelEdit([object]$Payload) {
    $app = $null; $workbook = $null; $success = $false
    $source = Full-Path $Payload.path; $temporary = Full-Path $Payload.temporary; $output = Full-Path $Payload.output
    Copy-Item -LiteralPath $source -Destination $temporary -Force; $receipts = [System.Collections.ArrayList]::new()
    try {
        $app = New-Object -ComObject Excel.Application; Safe-App $app; $app.AskToUpdateLinks = $false
        $workbook = $app.Workbooks.Open($temporary, 0, $false); $index = 0
        foreach ($op in @($Payload.operations)) {
            $sheet = $null; $range = $null
            try {
                if (Has-Field $op 'sheet') { $sheet = $workbook.Worksheets.Item([string]$op.sheet) }
                switch ([string]$op.op) {
                    'add_sheet' { $sheet = $workbook.Worksheets.Add(); $sheet.Name = [string]$op.name }
                    'delete_sheet' { $sheet.Delete() }
                    'rename_sheet' { $sheet.Name = [string]$op.name }
                    'set_cell' { $range = $sheet.Range([string]$op.cell); Set-Excel-CellValue $range $op.value }
                    'set_range' {
                        $range = $sheet.Range([string]$op.range); $values = @($op.values)
                        for ($r = 0; $r -lt $values.Count; $r++) { for ($c = 0; $c -lt @($values[$r]).Count; $c++) { $cell = $range.Cells.Item($r + 1, $c + 1); try { Set-Excel-CellValue $cell $values[$r][$c] } finally { Release-Com $cell } } }
                    }
                    'set_formula' { $range = $sheet.Range([string]$op.range); try { $range.Formula2 = [string]$op.formula } catch { $range.Formula = [string]$op.formula } }
                    'clear_range' { $range = $sheet.Range([string]$op.range); $range.Clear() }
                    'format_range' {
                        $range = $sheet.Range([string]$op.range)
                        if (Has-Field $op 'bold') { $range.Font.Bold = [bool]$op.bold }; if (Has-Field $op 'italic') { $range.Font.Italic = [bool]$op.italic }
                        if (Has-Field $op 'font_size') { $range.Font.Size = [double]$op.font_size }; if (Has-Field $op 'font_name') { $range.Font.Name = [string]$op.font_name }
                        if (Has-Field $op 'number_format') { $range.NumberFormat = [string]$op.number_format }; if (Has-Field $op 'fill_color') { $range.Interior.Color = Office-Color ([string]$op.fill_color) }
                        if (Has-Field $op 'font_color') { $range.Font.Color = Office-Color ([string]$op.font_color) }
                        if (Has-Field $op 'horizontal_alignment') { $range.HorizontalAlignment = @{ left = -4131; center = -4108; right = -4152 }[[string]$op.horizontal_alignment] }
                    }
                    'merge_range' { $range = $sheet.Range([string]$op.range); $range.Merge() }
                    'unmerge_range' { $range = $sheet.Range([string]$op.range); $range.UnMerge() }
                    'autofit' { $range = $sheet.Range([string]$op.range); $range.Columns.AutoFit() | Out-Null; $range.Rows.AutoFit() | Out-Null }
                    'sort_range' {
                        $range = $sheet.Range([string]$op.range); $key = $sheet.Range([string]$op.key); $sort = $sheet.Sort
                        try { $sort.SortFields.Clear(); [void]$sort.SortFields.Add($key, 0, $(if ([bool]$op.descending) { 2 } else { 1 })); $sort.SetRange($range); $sort.Header = $(if ((Has-Field $op 'header') -and [bool]$op.header) { 1 } else { 2 }); $sort.Apply() }
                        finally { Release-Com $sort; Release-Com $key }
                    }
                    'filter_range' { $range = $sheet.Range([string]$op.range); if (Has-Field $op 'criteria') { [void]$range.AutoFilter([int]$op.field, [string]$op.criteria) } else { [void]$range.AutoFilter([int]$op.field) } }
                    'add_chart' {
                        $range = $sheet.Range([string]$op.source_range); $charts = $sheet.ChartObjects(); $chartObject = $charts.Add($(if (Has-Field $op 'left') { $op.left } else { 20 }), $(if (Has-Field $op 'top') { $op.top } else { 20 }), $(if (Has-Field $op 'width') { $op.width } else { 480 }), $(if (Has-Field $op 'height') { $op.height } else { 280 }))
                        try { $chartObject.Name = [string]$op.name; $chartObject.Chart.SetSourceData($range); $chartObject.Chart.ChartType = $(if (Has-Field $op 'chart_type') { [int]$op.chart_type } else { 51 }); if (Has-Field $op 'title') { $chartObject.Chart.HasTitle = $true; $chartObject.Chart.ChartTitle.Text = [string]$op.title } }
                        finally { Release-Com $chartObject; Release-Com $charts }
                    }
                    'delete_chart' { $charts = $sheet.ChartObjects(); try { $chart = $charts.Item([string]$op.name); try { $chart.Delete() } finally { Release-Com $chart } } finally { Release-Com $charts } }
                    'set_property' { Set-BuiltinProperty $workbook ([string]$op.name) $op.value }
                    default { throw "Unsupported excel operation: $($op.op)" }
                }
                Add-Receipt $receipts $index $op; $index++
            } finally { Release-Com $range; Release-Com $sheet }
        }
        $workbook.Save(); $success = $true
    } finally {
        if ($null -ne $workbook) { try { $workbook.Close($success) } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $workbook; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        if (-not $success -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
    Promote-OfficeOutput $temporary $output
    return File-Result 'excel.edit' $output @{ application = 'excel'; source = $source; operation_count = $receipts.Count; receipts = @($receipts) }
}

function Inspect-PowerPoint([object]$Payload) {
    $app = $null; $presentation = $null
    try {
        $path = Full-Path $Payload.path; $app = New-Object -ComObject PowerPoint.Application; Safe-App $app
        $presentation = $app.Presentations.Open($path, -1, 0, 0); $slides = @()
        foreach ($slide in @($presentation.Slides)) {
            $shapes = @()
            foreach ($shape in @($slide.Shapes)) { $text = ''; try { if ($shape.HasTextFrame -and $shape.TextFrame.HasText) { $text = [string]$shape.TextFrame.TextRange.Text } } catch {}; $shapes += [ordered]@{ name = $shape.Name; type = $shape.Type; left = $shape.Left; top = $shape.Top; width = $shape.Width; height = $shape.Height; text = $text.Substring(0, [Math]::Min(500, $text.Length)) }; Release-Com $shape }
            $slides += [ordered]@{ index = $slide.SlideIndex; shapes = $shapes }; Release-Com $slide
        }
        return [ordered]@{ ok = $true; action = 'powerpoint.inspect'; application = 'powerpoint'; path = $path; slides = $slides }
    } finally {
        if ($null -ne $presentation) { try { $presentation.Close() } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $presentation; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-PowerPointEdit([object]$Payload) {
    $app = $null; $presentation = $null; $success = $false
    $source = Full-Path $Payload.path; $temporary = Full-Path $Payload.temporary; $output = Full-Path $Payload.output
    Copy-Item -LiteralPath $source -Destination $temporary -Force; $receipts = [System.Collections.ArrayList]::new()
    try {
        $app = New-Object -ComObject PowerPoint.Application; Safe-App $app; $presentation = $app.Presentations.Open($temporary, 0, 0, 0); $index = 0
        foreach ($op in @($Payload.operations)) {
            $slide = $null; $shape = $null
            try {
                if (Has-Field $op 'index' -and [string]$op.op -notin @('add_slide','move_slide')) { $slide = $presentation.Slides.Item([int]$op.index) }
                switch ([string]$op.op) {
                    'add_slide' { $position = if (Has-Field $op 'index') { [int]$op.index } else { $presentation.Slides.Count + 1 }; $slide = $presentation.Slides.Add($position, $(if (Has-Field $op 'layout') { [int]$op.layout } else { 12 })); if (Has-Field $op 'title') { try { $slide.Shapes.Title.TextFrame.TextRange.Text = [string]$op.title } catch {} } }
                    'delete_slide' { $slide.Delete() }
                    'move_slide' { $slide = $presentation.Slides.Item([int]$op.index); $slide.MoveTo([int]$op.to) }
                    'set_slide_title' { if ($slide.Shapes.HasTitle) { $slide.Shapes.Title.TextFrame.TextRange.Text = [string]$op.text } else { $shape = $slide.Shapes.AddTextbox(1, 20, 10, 680, 50); $shape.TextFrame.TextRange.Text = [string]$op.text } }
                    'add_textbox' { $shape = $slide.Shapes.AddTextbox(1, [double]$op.left, [double]$op.top, [double]$op.width, [double]$op.height); $shape.TextFrame.TextRange.Text = [string]$op.text; if (Has-Field $op 'name') { $shape.Name = [string]$op.name }; if (Has-Field $op 'font_size') { $shape.TextFrame.TextRange.Font.Size = [double]$op.font_size }; if (Has-Field $op 'bold') { $shape.TextFrame.TextRange.Font.Bold = if ([bool]$op.bold) { -1 } else { 0 } }; if (Has-Field $op 'color') { $shape.TextFrame.TextRange.Font.Color.RGB = Office-Color ([string]$op.color) } }
                    'replace_text' { foreach ($candidate in @($presentation.Slides)) { if ((Has-Field $op 'index') -and $candidate.SlideIndex -ne [int]$op.index) { Release-Com $candidate; continue }; foreach ($s in @($candidate.Shapes)) { try { if ($s.HasTextFrame -and $s.TextFrame.HasText) { $current = [string]$s.TextFrame.TextRange.Text; $s.TextFrame.TextRange.Text = if ((Has-Field $op 'match_case') -and [bool]$op.match_case) { $current.Replace([string]$op.find, [string]$op.replace) } else { $current -replace [regex]::Escape([string]$op.find), [string]$op.replace } } } catch {}; Release-Com $s }; Release-Com $candidate } }
                    'add_image' { $shape = $slide.Shapes.AddPicture((Full-Path $op.path), 0, -1, [double]$op.left, [double]$op.top, [double]$op.width, [double]$op.height); if (Has-Field $op 'name') { $shape.Name = [string]$op.name } }
                    'add_table' { $rows = @($op.rows); $cols = 1; foreach ($row in $rows) { $cols = [Math]::Max($cols, @($row).Count) }; $shape = $slide.Shapes.AddTable([Math]::Max(1,$rows.Count), $cols, [double]$op.left, [double]$op.top, [double]$op.width, [double]$op.height); if (Has-Field $op 'name') { $shape.Name = [string]$op.name }; for ($r=0; $r -lt $rows.Count; $r++) { for ($c=0; $c -lt @($rows[$r]).Count; $c++) { $cell = $shape.Table.Cell($r+1,$c+1); try { $cell.Shape.TextFrame.TextRange.Text = [string]$rows[$r][$c] } finally { Release-Com $cell } } } }
                    'add_shape' { $shape = $slide.Shapes.AddShape([int]$op.shape_type, [double]$op.left, [double]$op.top, [double]$op.width, [double]$op.height); if (Has-Field $op 'name') { $shape.Name = [string]$op.name }; if (Has-Field $op 'text') { $shape.TextFrame.TextRange.Text = [string]$op.text }; if (Has-Field $op 'fill_color') { $shape.Fill.ForeColor.RGB = Office-Color ([string]$op.fill_color) }; if (Has-Field $op 'line_color') { $shape.Line.ForeColor.RGB = Office-Color ([string]$op.line_color) } }
                    'update_shape' { $shape = $slide.Shapes.Item([string]$op.name); foreach ($field in @('left','top','width','height')) { if (Has-Field $op $field) { $shape.$field = [double]$op.$field } }; if (Has-Field $op 'text') { $shape.TextFrame.TextRange.Text = [string]$op.text }; if (Has-Field $op 'fill_color') { $shape.Fill.ForeColor.RGB = Office-Color ([string]$op.fill_color) }; if (Has-Field $op 'line_color') { $shape.Line.ForeColor.RGB = Office-Color ([string]$op.line_color) } }
                    'delete_shape' { $shape = $slide.Shapes.Item([string]$op.name); $shape.Delete() }
                    'set_background' { $slide.FollowMasterBackground = 0; $slide.Background.Fill.ForeColor.RGB = Office-Color ([string]$op.color) }
                    'set_property' { Set-BuiltinProperty $presentation ([string]$op.name) $op.value }
                    default { throw "Unsupported powerpoint operation: $($op.op)" }
                }
                Add-Receipt $receipts $index $op; $index++
            } finally { Release-Com $shape; Release-Com $slide }
        }
        $presentation.Save(); $success = $true
    } finally {
        if ($null -ne $presentation) { try { $presentation.Close() } catch {} }; if ($null -ne $app) { try { $app.Quit() } catch {} }
        Release-Com $presentation; Release-Com $app; [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        if (-not $success -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
    Promote-OfficeOutput $temporary $output
    return File-Result 'powerpoint.edit' $output @{ application = 'powerpoint'; source = $source; operation_count = $receipts.Count; receipts = @($receipts) }
}
