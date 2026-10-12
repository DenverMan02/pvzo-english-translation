$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$gameRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$sourceRoot = 'C:\Users\fuyu\Documents\Tencent Files\2273047245\nt_qq\nt_data\Pic\2026-09\Ori'
$outputRoot = Join-Path $gameRoot 'webroot\image\item'
$patchRoot = $PSScriptRoot
$specs = [System.Collections.Generic.List[object]]::new()

function Add-Grid($file, [string[]]$labels, [int[]]$centersX, [int[]]$centersY) {
    for ($i = 0; $i -lt $labels.Length; $i++) {
        $row = [int][Math]::Floor($i / $centersX.Length)
        $col = $i % $centersX.Length
        if ($row -ge $centersY.Length) { throw "Too many labels for $file" }
        $label = $labels[$i]
        if ($label -match '^66\d{3}$') {
            $itemId = [int]$label
        } elseif ($label -match '^\d+-\d+$') {
            $parts = $label.Split('-')
            $itemId = 65000 + ([int]$parts[0] * 100) + [int]$parts[1]
        } else {
            $itemId = 65000 + [int]$label
        }
        $specs.Add([pscustomobject]@{
            Id = $itemId; Label = $label; File = $file
            Left = $centersX[$col] - 33; Top = $centersY[$row] - 33
        })
    }
}

Add-Grid 'f1eff8c096a9d247af6057db286f5cb1.png' @(
    '6','7','8','9','10','11','12','13',
    '15','16','17','18','21','24','1-1','1-2',
    '1-3','1-4','1-5','1-6','1-7','1-8','1-9','1-10',
    '1-11','1-12','1-13','1-14','1-15','1-16','1-17','1-18'
) @(54,151,248,345,442,539,636,733) @(49,145,239,333)

Add-Grid '324e55c9d1e03520e10bc24305812892.png' @('2','3','4') @(41,138,237) @(42)

Add-Grid '42df31209557e175959ebaeb35c60e81.png' @(
    '1-19','1-20','1-21','1-22','1-23','1-24','1-25','2-1',
    '2-2','2-3','2-4','2-5','2-6','2-7','2-8','2-9',
    '2-10','2-11','2-12','2-13','2-14','2-15','2-16','2-17',
    '2-18','2-19','2-20','2-21','2-22','2-23','2-24','2-25'
) @(31,126,221,316,411,506,601,696) @(68,164,258,352)

Add-Grid '80edba60bb9ee7aac842a7ed28101546.png' @(
    '3-1','3-2','3-3','3-4','3-5','3-6','3-7','3-8',
    '3-9','3-10','3-11','3-12','3-13','3-14','3-15','3-16',
    '3-17','3-18','3-19','3-20','3-21','3-22','3-23','3-24',
    '3-25','4-1','4-2','4-3','4-4','4-5','4-6','4-7'
) @(67,164,261,358,455,552,649,746) @(66,160,254,348)

Add-Grid 'f41800801e90202495da5ebb9597d8a4.png' @(
    '4-8','4-9','4-10','4-11','4-12','4-13','4-14','4-15',
    '4-16','4-17','4-18','4-19','4-20','4-21','4-22','4-23',
    '4-24','4-25','5-1','5-2','5-3','5-4','5-5','5-6',
    '5-7','5-8','5-9','5-10','5-11','5-12','5-13','5-14'
) @(64,164,264,364,464,564,664,764) @(54,148,242,336)

Add-Grid 'a7fa01a6bafa9bca26a85ffbf5bb377b.png' @(
    '5-15','5-16','5-17','5-18','5-19','5-20','5-21','5-22',
    '5-23','5-24','5-25','6-1','6-2','6-3','6-4','6-5',
    '6-6','6-7','6-8','6-9','6-10','6-11','6-12','6-13',
    '6-14','6-15','6-16','6-17','6-18','6-19','6-20','6-21'
) @(49,146,243,340,437,534,631,728) @(59,154,249,344)

Add-Grid 'bcfe17d73ccf83187ae1ec44eae7402c.png' @(
    '6-22','6-23','6-24','6-25','66001','66101','66201','66301',
    '66401','66501'
) @(40,135,230,325,420,515,610,705) @(43,138)

$images = @{}
$crops = [System.Collections.Generic.List[object]]::new()
foreach ($spec in $specs) {
    if (-not $images.ContainsKey($spec.File)) {
        $path = Join-Path $sourceRoot $spec.File
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing source image: $path" }
        $images[$spec.File] = [Drawing.Bitmap]::FromFile($path)
    }
    $source = $images[$spec.File]
    $crop = [Drawing.Bitmap]::new(66,66,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
    for ($y = 0; $y -lt 66; $y++) {
        for ($x = 0; $x -lt 66; $x++) {
            $sx = $spec.Left + $x; $sy = $spec.Top + $y
            if ($sx -ge 0 -and $sy -ge 0 -and $sx -lt $source.Width -and $sy -lt $source.Height) {
                $crop.SetPixel($x,$y,$source.GetPixel($sx,$sy))
            }
        }
    }
    $crops.Add([pscustomobject]@{Spec=$spec; Bitmap=$crop})
}

# The provided sources are screenshots, so their bottom-right inventory quantities are baked
# into the crop. Keep the pixel art intact instead of painting over it and damaging the puzzle.

$manifest = [System.Collections.Generic.List[string]]::new()
$manifest.Add('item_id,label,source_image,crop_left,crop_top,crop_size,output_size,quantity_overlay')
foreach ($entry in $crops) {
    $spec = $entry.Spec
    $output = Join-Path $outputRoot ($spec.Id.ToString() + '.png')
    $scaled = [Drawing.Bitmap]::new(60,60,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($scaled)
    $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.DrawImage($entry.Bitmap,0,0,60,60)
    $graphics.Dispose()
    $scaled.Save($output,[Drawing.Imaging.ImageFormat]::Png)
    $scaled.Dispose(); $entry.Bitmap.Dispose()
    $manifest.Add("$($spec.Id),$($spec.Label),$($spec.File),$($spec.Left),$($spec.Top),66,60,source-quantity-baked-in")
}
foreach ($bitmap in $images.Values) { $bitmap.Dispose() }
[IO.File]::WriteAllLines((Join-Path $patchRoot 'puzzle_icon_crop_manifest.csv'),$manifest,[Text.UTF8Encoding]::new($true))
Write-Output "Wrote $($crops.Count) puzzle item icons to $outputRoot"
