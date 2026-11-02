# Acceso directo local; no activa inicio con Windows ni publica rutas privadas.
$ErrorActionPreference = 'Stop'
$thornProject = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$thornBinary = Join-Path $thornProject 'dist\desktop.dist\THORN.exe'
if (-not (Test-Path -LiteralPath $thornBinary -PathType Leaf)) { throw 'Primero compila T.H.O.R.N.' }
$thornShortcutPath = Join-Path $thornProject 'T.H.O.R.N - Bastion 9.lnk'
$thornShell = New-Object -ComObject WScript.Shell
$thornShortcut = $thornShell.CreateShortcut($thornShortcutPath)
$thornShortcut.TargetPath = $thornBinary
$thornShortcut.Arguments = ('--data-dir "{0}"' -f $thornProject)
$thornShortcut.WorkingDirectory = $thornProject
$thornShortcut.IconLocation = ($thornBinary + ',0')
$thornShortcut.WindowStyle = 1
$thornShortcut.Description = 'T.H.O.R.N Bastion 9 - perfil local cifrado'
$thornShortcut.Save()
Write-Output ('Acceso directo creado: ' + $thornShortcutPath)
