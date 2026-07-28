$ErrorActionPreference = "Continue"
$log = "C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\cleanup-zipmaster-admin.log"
"[$(Get-Date -Format o)] cleanup start" | Out-File -FilePath $log -Encoding UTF8 -Append
Get-Process explorer -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$zip = "C:\Users\45543\AppData\Local\winToolBox\Tools\zip"
if (Test-Path $zip) {
  Remove-Item -LiteralPath $zip -Recurse -Force -ErrorAction Continue
}
foreach ($k in 'ZipMaster.zip','ZipMaster.7z','ZipMaster.rar','ZipMaster.tar','ZipMaster.gz') {
  $p = "HKLM:\Software\Classes\$k"
  if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Continue }
}
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.zip\UserChoice' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.7z\UserChoice' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.rar\UserChoice' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.tar\UserChoice' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.gz\UserChoice' -Force -ErrorAction SilentlyContinue
Start-Process explorer.exe
"[$(Get-Date -Format o)] cleanup end" | Out-File -FilePath $log -Encoding UTF8 -Append
