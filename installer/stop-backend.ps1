# Arrête le backend Coquille s'il tourne (installé, ou lancé par run-backend.bat) :
# ses fichiers seraient verrouillés pendant une mise à jour ou une désinstallation.
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'coquille\.app' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
