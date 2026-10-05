# Publicacion segura en GitHub

Repositorio destino: https://github.com/jeraldo114/Proyecto_T.H.O.R.N

## Que se comparte

Codigo, pruebas, documentacion y la plantilla .env.example. .gitignore excluye
el .env real, entornos virtuales, logs, datos, respaldos y claves privadas.
El repositorio Git termina en esta carpeta; no incorpora el perfil de Windows.

El correo del autor de los commits se configura solo en este repositorio con
el noreply de GitHub, para no publicar un correo personal. No se guardan tokens
de GitHub en la URL origin, en .env ni en archivos del proyecto.

## Revisar antes de cada commit

```powershell
git status --short
git add -- .
.\.venv\Scripts\python.exe tools/check_repository.py
git diff --cached --stat
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v
git commit -m "Describe el cambio realizado"
.\.venv\Scripts\python.exe tools/check_repository.py --history
```

El control lee los blobs del indice o los commits. Asi detecta un archivo privado
que alguien haya agregado con git add -f. Si falla, no crear el commit ni hacer
push hasta corregir el problema. No muestra los valores privados encontrados.

La instalacion local activa .githooks/pre-commit mediante core.hooksPath. Git
no activa hooks al clonar; en otra maquina ejecutar:

```powershell
git config --local core.hooksPath .githooks
```

Los hooks son una ayuda local y pueden saltarse. La CI vuelve a comprobar los
archivos confirmados y el historial, pero eso ocurre despues del push: no sustituye
el control local ni la proteccion de push de GitHub.

## Dependencias verificables

requirements.in contiene las dependencias directas. requirements.txt fija tambien
las dependencias transitivas y los hashes SHA-256 de los paquetes. Instalar asi:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
```

requirements-dev.in y requirements-dev.txt agregan pip-tools y pip-audit para
mantenimiento. No son dependencias necesarias para ejecutar el asistente.

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --output-file requirements.txt requirements.in
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --no-emit-index-url --no-emit-trusted-host --output-file requirements-dev.txt requirements-dev.in
.\.venv\Scripts\python.exe -m pip_audit --strict --disable-pip -r requirements.txt
```

Actualizar el archivo .in, regenerar ambos .txt y comprobar pruebas y auditoria
antes de aceptar una actualizacion. Los hashes verifican los archivos descargados;
no garantizan que el codigo de un paquete carezca de vulnerabilidades.

## Controles que contiene el repositorio

- CI ejecuta pruebas en Windows y Linux, verifica Django y audita el historial.
- Las acciones de GitHub se fijan a commits completos y el token de CI solo tiene
  permisos de lectura. Checkout no conserva credenciales en el runner.
- La auditoria de dependencias falla si encuentra vulnerabilidades conocidas
  o no puede completar la consulta. No usa credenciales reales de MySQL.
- Dependabot propone actualizaciones semanales de paquetes y acciones. El
  propietario revisa sus PR; no hay fusion automatica.
- CODEOWNERS identifica al propietario como revisor del codigo.

## Ajustes del repositorio en GitHub

Estos ajustes se revisan en GitHub; los archivos del proyecto no los activan:

1. Verificar la visibilidad. Mantener privado el repositorio mientras el codigo
   y las decisiones de publicacion se revisan es la opcion mas conservadora.
2. Habilitar alertas de Dependabot y, cuando esten disponibles para el plan y
   visibilidad, secret scanning y push protection.
3. Crear una regla para main que impida force pushes y borrado y exija las
   comprobaciones CI antes de integrar cambios. Exigir revisiones cuando haya
   otro revisor disponible.
4. Habilitar reportes privados de vulnerabilidades cuando esten disponibles.
5. Mantener 2FA o passkeys en la cuenta y usar Git Credential Manager o SSH para
   autenticar Git. No poner contrasenas ni tokens en comandos visibles.

No se aplica push --force. Si el remoto deja de estar vacio antes del primer
push, revisar su historial y reconciliar los cambios en lugar de sobrescribirlo.

## Si alguna clave se filtra

Revocar la clave primero y generar otra. Quitarla del codigo actual no la quita
de commits antiguos, forks o clones. Coordinar la limpieza del historial y los
avisos necesarios despues de revocarla.

## Referencias

- [Uso seguro de GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use)
- [Opciones de Dependabot](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)
- [pip-audit](https://github.com/pypa/pip-audit)
- [pip-tools y hashes](https://pip-tools.readthedocs.io/en/latest/)
