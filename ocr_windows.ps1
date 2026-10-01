param([Parameter(Mandatory=$true)][string]$PdfPath)

# Windows 10/11 built-in OCR. The PDF is treated as data only; its text is
# never executed as PowerShell or interpreted as an instruction.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$opMethods = [System.WindowsRuntimeSystemExtensions].GetMethods()

function Await([object]$Operation, [type]$ResultType) {
    if ($null -eq $ResultType) {
        $method = $opMethods | Where-Object {
            $_.Name -eq "AsTask" -and !$_.IsGenericMethod -and $_.GetParameters().Count -eq 1
        } | Select-Object -First 1
        $task = $method.Invoke($null, @($Operation))
    } else {
        $method = $opMethods | Where-Object {
            $_.Name -eq "AsTask" -and $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1
        } | Select-Object -First 1
        $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    }
    return $task.GetAwaiter().GetResult()
}

$ocrType = [Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
$languageType = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$ocr = $ocrType::TryCreateFromLanguage([Windows.Globalization.Language]::new("zh-Hans-CN"))
if ($null -eq $ocr) { $ocr = $ocrType::TryCreateFromUserProfileLanguages() }
if ($null -eq $ocr) { throw "Windows 中文 OCR 引擎不可用，请在系统语言设置中安装中文（简体）OCR。" }

$storageType = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$file = Await ($storageType::GetFileFromPathAsync($PdfPath)) $storageType
$pdfType = [Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$pdf = Await ($pdfType::LoadFromFileAsync($file)) $pdfType
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime]

for ($index = 0; $index -lt $pdf.PageCount; $index++) {
    $page = $pdf.GetPage($index)
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]::new()
    Await ($page.RenderToStreamAsync($stream)) $null | Out-Null
    $stream.Seek(0)
    $create = $decoderType.GetMethods() | Where-Object {
        $_.Name -eq "CreateAsync" -and $_.GetParameters().Count -eq 1
    } | Select-Object -First 1
    $decoder = Await ($create.Invoke($null, @($stream))) $decoderType
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) $bitmapType
    $result = Await ($ocr.RecognizeAsync($bitmap)) $resultType
    Write-Output "===PAGE $($index + 1)==="
    Write-Output $result.Text
}
