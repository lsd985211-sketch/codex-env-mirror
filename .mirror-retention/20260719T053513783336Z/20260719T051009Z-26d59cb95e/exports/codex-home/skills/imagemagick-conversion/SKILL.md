---
name: imagemagick-conversion
description: >
  Convert, resize, crop, rotate, optimize, inspect, or batch-process local images
  with ImageMagick. Use for format conversion, thumbnails, quality changes,
  metadata stripping, compositing, and bounded batch image operations.
---

# ImageMagick Conversion

## Role Boundaries

Use ImageMagick for local image manipulation. Acquire remote inputs through the resource layer first. Preserve originals unless the user explicitly requests in-place changes.

## Preflight

```powershell
magick -version
```

Inspect the input before mutation:

```powershell
magick identify 'C:\path\to\image.png'
```

Resolve output paths explicitly and create output directories with PowerShell:

```powershell
$out = 'C:\path\to\output'
New-Item -ItemType Directory -Path $out -Force | Out-Null
```

## Common Operations

Convert format:

```powershell
magick 'input.jpg' 'output.png'
magick 'input.png' -quality 85 'output.webp'
```

Resize while preserving aspect ratio:

```powershell
magick 'input.jpg' -resize '1200x>' 'output.jpg'
magick 'input.jpg' -resize '800x600' 'output.jpg'
magick 'input.jpg' -thumbnail '320x320^' -gravity center -extent 320x320 'thumb.jpg'
```

Crop or rotate:

```powershell
magick 'input.jpg' -crop '800x600+100+50' +repage 'cropped.jpg'
magick 'input.jpg' -rotate 90 'rotated.jpg'
magick 'input.jpg' -flip 'flipped-vertical.jpg'
magick 'input.jpg' -flop 'flipped-horizontal.jpg'
```

Optimize and remove metadata:

```powershell
magick 'input.jpg' -strip -quality 82 'optimized.jpg'
magick 'input.png' -strip -define webp:method=6 -quality 82 'optimized.webp'
```

Composite:

```powershell
magick 'background.png' 'overlay.png' -gravity southeast -geometry +20+20 -composite 'combined.png'
```

## Batch Processing

Prefer explicit PowerShell enumeration over shell globs that behave differently across platforms:

```powershell
$source = 'C:\images\source'
$output = 'C:\images\web'
New-Item -ItemType Directory -Path $output -Force | Out-Null

Get-ChildItem -LiteralPath $source -File -Filter '*.jpg' | ForEach-Object {
    $target = Join-Path $output ($_.BaseName + '.webp')
    magick $_.FullName -auto-orient -strip -resize '1920x>' -quality 82 $target
    if ($LASTEXITCODE -ne 0) { throw "ImageMagick failed for $($_.FullName)" }
}
```

Do not use `mogrify` in place unless destructive modification is explicitly intended. For a batch, write to a separate output directory and verify representative files before replacing originals.

## Quality Guidance

- JPEG photos: quality 80-88 is a practical starting range.
- WebP photos: quality 78-85; verify visual artifacts.
- PNG: lossless by default; resizing usually reduces size more than quality flags.
- Use `-auto-orient` for camera images before resizing.
- Use `-strip` only when metadata is not required.
- Preserve transparency by choosing PNG or WebP output.

## Verification

```powershell
magick identify 'output.webp'
Get-Item -LiteralPath 'output.webp' | Select-Object FullName,Length
```

For batch work, compare input/output counts and inspect at least one small, one large, and one transparent image when applicable.

## Output Contract

Return the exact output paths, dimensions, formats, sizes, and any failed inputs. State whether originals were preserved or modified.
