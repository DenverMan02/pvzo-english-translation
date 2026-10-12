param(
    [string]$Webroot = (Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'webroot')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$itemDirectory = Join-Path $Webroot 'image\item'
$backupDirectory = Join-Path $PSScriptRoot 'original-62px'
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

foreach ($itemId in 2037..2040) {
    $target = Join-Path $itemDirectory "$itemId.png"
    $backup = Join-Path $backupDirectory "$itemId.png"
    if (-not (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $target -Destination $backup
    }

    $source = [System.Drawing.Bitmap]::FromFile($backup)
    try {
        if ($source.Width -ne 62 -or $source.Height -ne 62) {
            throw "Unexpected source size for $itemId`: $($source.Width)x$($source.Height)"
        }
        # BagItemIcon places item art at (6, 6) inside a 62-pixel slot.
        # A 50-pixel resource therefore stays centered and fully visible.
        $output = New-Object System.Drawing.Bitmap(50, 50, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($output)
            try {
                $graphics.Clear([System.Drawing.Color]::Transparent)
                $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                $graphics.DrawImage($source, [System.Drawing.Rectangle]::new(0, 0, 50, 50),
                    [System.Drawing.Rectangle]::new(0, 0, 62, 62), [System.Drawing.GraphicsUnit]::Pixel)
            }
            finally { $graphics.Dispose() }
            $output.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally { $output.Dispose() }
    }
    finally { $source.Dispose() }
}
