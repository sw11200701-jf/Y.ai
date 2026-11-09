param([Parameter(Mandatory=$true)][string]$ImagePath,[Parameter(Mandatory=$true)][string]$LanguageTag)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'} | Select-Object -First 1
function Await-Operation($operation,[Type]$resultType){
    $task=$asTask.MakeGenericMethod($resultType).Invoke($null,@($operation))
    return $task.GetAwaiter().GetResult()
}
$language=[Windows.Globalization.Language]::new($LanguageTag)
if(-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($language)){throw 'OCR_LANGUAGE_UNAVAILABLE'}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if($null -eq $engine){throw 'OCR_ENGINE_UNAVAILABLE'}
$file=Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=$null;$bitmap=$null
try{
    $stream=Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension){throw 'OCR_IMAGE_DIMENSION_EXCEEDED'}
    $bitmap=Await-Operation ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result=Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $angle=if($null -eq $result.TextAngle){0.0}else{[double]$result.TextAngle.Value}
    if([Math]::Abs($angle) -gt 0.1){throw 'OCR_ROTATED_GEOMETRY_UNQUALIFIED'}
    $lines=@()
    foreach($line in $result.Lines){
        if(-not $line.Words.Count){continue}
        $left=[double]::PositiveInfinity;$top=[double]::PositiveInfinity;$right=0.0;$bottom=0.0
        foreach($word in $line.Words){
            $rect=$word.BoundingRect
            $left=[Math]::Min($left,$rect.X);$top=[Math]::Min($top,$rect.Y)
            $right=[Math]::Max($right,$rect.X+$rect.Width);$bottom=[Math]::Max($bottom,$rect.Y+$rect.Height)
        }
        $lines+=@{text=$line.Text;region=@($left,$top,$right,$bottom)}
    }
    @{adapter='windows-media-ocr-v1';language_tag=$engine.RecognizerLanguage.LanguageTag;width=[int]$decoder.PixelWidth;height=[int]$decoder.PixelHeight;text_angle=$angle;lines=@($lines);os_version=[Environment]::OSVersion.Version.ToString()} | ConvertTo-Json -Depth 8 -Compress
}finally{
    if($null -ne $bitmap){$bitmap.Dispose()}
    if($null -ne $stream){$stream.Dispose()}
}
