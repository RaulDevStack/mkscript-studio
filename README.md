# MKSCRIPT Studio

Taller visual para **crear, personalizar, editar, validar y guardar scripts Bash** en Linux.
Es la evolución de [mkscript](https://github.com/RaulDevStack/mkscript): mantiene sus ideas
(plantillas, nombres validados, extensión `.sh` automática, permisos de ejecución, no sobrescribir
archivos y abrir el resultado en un editor) y las lleva a una aplicación de escritorio GTK 4 con
un editor integrado.

El código Bash es siempre visible y editable. Los formularios solo ayudan a generarlo.

## Funcionalidades

| Sección | Qué incluye |
| --- | --- |
| **Inicio** | Nuevo script, abrir archivo, recientes, plantillas favoritas, accesos rápidos y resumen de preferencias. |
| **Biblioteca** | 7 plantillas por categorías, búsqueda, filtro por categoría y favoritas, formulario de parámetros validado, aviso de riesgos, vista previa en tiempo real, «Abrir en Studio» y «Generar y guardar». Plantillas personalizadas (crear desde Studio y eliminar). |
| **Studio** | Editor GtkSourceView con resaltado Bash, números de línea, indentación configurable, deshacer/rehacer, buscar y reemplazar (mayúsculas, palabra completa, regex), autocompletado con Tab, panel de archivos de la carpeta, panel de generación con vista previa, panel de problemas (bash -n y ShellCheck con marcas en el margen) y panel de salida de ejecución. Barra de estado con ruta, estado de guardado, línea/columna e indentación. |
| **Proyectos** | Crear, añadir carpeta existente, renombrar, duplicar, quitar de la lista (sin borrar archivos), abrir la carpeta y abrir sus scripts. |
| **Preferencias** | Shell, estilo de comentarios, modo estricto, colores, carpeta de salida, permisos, copias de seguridad, editor, autocompletado y tema. Se guardan al momento. |

### Plantillas

| Id | Plantilla | Riesgo |
| --- | --- | --- |
| `minimal` | Script mínimo | Bajo |
| `cli-tool` | Herramienta CLI con argumentos (`--help`, `--version`, opciones, posicional) | Bajo |
| `backup` | Copia de seguridad con `tar` fechada y exclusiones | Medio |
| `cleanup-temp` | Limpieza controlada de temporales (simulación por defecto, `--apply` y confirmación) | Alto |
| `process-monitor` | Monitorización de un proceso con `pgrep`/`ps` | Bajo |
| `file-organizer` | Organización de archivos por extensión (simulación por defecto, `mv -n`) | Medio |
| `service-check` | Estado de servicios con `systemctl is-active` | Bajo |

Las plantillas que pueden modificar datos solo actúan con `--apply`, comprueban sus rutas y
rechazan carpetas del sistema o la carpeta personal completa. Las excepciones legítimas al modo
estricto (`pgrep` sin coincidencias, `systemctl is-active` de un servicio caído) se tratan de forma
explícita en `if` o con `|| true` comentado. Generar una plantilla nunca ejecuta nada.

### Autocompletado

- `ec` + <kbd>Tab</kbd> → `echo`; `mkd` → `mkdir`; `gre` → `grep`.
- Si hay varias coincidencias (`pr` → `printf`, `printenv`), Tab amplía el prefijo común y muestra
  una lista. ↑/↓ para moverse, <kbd>Tab</kbd> o <kbd>Intro</kbd> para confirmar, <kbd>Esc</kbd> para cerrar.
- Sin coincidencias, al principio de línea, en comentarios o dentro de comillas simples, Tab indenta
  con normalidad. Con texto seleccionado, Tab indenta la selección.
- Solo se sustituye el prefijo anterior al cursor.
- Distingue funciones del archivo, builtins, palabras reservadas, órdenes, variables (`$VAR`,
  `${VAR}`) y rutas (`./`, `~/`, `/`). Sugiere `in` tras `for x`. Las órdenes del `PATH` son opcionales.
- Las sugerencias aparecen mientras se escribe (configurable) y con <kbd>Ctrl</kbd>+<kbd>Espacio</kbd>.
- El contenido del editor se analiza como texto; nunca se ejecuta para obtener sugerencias.

### Seguridad

- **No se sobrescribe nada en silencio.** «Generar y guardar» pide confirmación si el archivo existe
  y el diálogo «Guardar como» de GTK también lo hace.
- **Copias de seguridad** con fecha y hora (`nombre.sh.AAAAMMDD-HHMMSS.bak`, o `-1`, `-2`… si ya
  existe una) antes de sobrescribir. Se crean con apertura exclusiva y nunca se borran solas. En
  Studio, la copia del archivo original se hace la primera vez que se sobrescribe en la sesión; los
  guardados siguientes del mismo documento no generan más copias.
- **Escritura atómica** (archivo temporal + `os.replace`), conservando los permisos existentes. La `x`
  solo se añade donde ya hay `r`. Si el archivo cambió en el disco tras abrirlo, se pide confirmación.
- **Comprobar sintaxis** usa `bash --noprofile --norc -n` por la entrada estándar, sin `BASH_ENV`, con
  límite de tiempo y en un hilo aparte. No ejecuta el script. Una sintaxis correcta **no** demuestra
  que el script sea seguro.
- **Ejecutar** es una acción aparte que exige guardar primero. Muestra el archivo, el intérprete, el
  directorio, que no se eleva ningún privilegio y las órdenes potencialmente peligrosas detectadas, y
  pide confirmación. Usa una lista de argumentos (`shell=False`), stdin cerrado y nunca `sudo`. Se
  puede detener (SIGTERM al grupo y SIGKILL tras 3 s). **No es un sandbox**: el script se ejecuta con
  los permisos de tu usuario.

## Requisitos

- Linux con Python ≥ 3.11
- GTK ≥ 4.10, libadwaita ≥ 1.4, GtkSourceView 5 y PyGObject
- `bash` (comprobación de sintaxis y ejecución)
- Opcional: `shellcheck`

| Distribución | Dependencias del sistema |
| --- | --- |
| Debian 13 / Ubuntu 24.04 o posterior | `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 shellcheck` |
| Fedora 39 o posterior | `sudo dnf install python3-gobject gtk4 libadwaita gtksourceview5 ShellCheck` |
| Arch Linux | `sudo pacman -S python-gobject gtk4 libadwaita gtksourceview5 shellcheck` |

Estas órdenes instalan paquetes del sistema; revísalas antes de ejecutarlas.

## Instalación y ejecución

### Uso directo desde el repositorio (recomendado)

No hace falta pip ni entorno virtual: el script `./mkscript-studio` arranca la aplicación desde el
código del repositorio.

Clonando solo la rama `release` y sin historial, `git pull` descarga únicamente lo imprescindible.

Primera vez:

```bash
git clone --branch release --single-branch --depth 1 https://github.com/RaulDevStack/mkscript-studio.git
```

```bash
cd mkscript-studio
```

```bash
./mkscript-studio --install-deps
```

`--install-deps` muestra la orden que va a ejecutar (apt, dnf o pacman) y pide la contraseña de sudo.
Solo hace falta una vez.

Arrancar (también se le puede pasar un archivo: `./mkscript-studio ~/Scripts/backup.sh`):

```bash
./mkscript-studio
```

Actualizar a la última versión:

```bash
git pull && ./mkscript-studio
```

Opcional: añadirlo al menú del escritorio. El lanzador apunta a este repositorio, así que tras cada
`git pull` el menú abre la versión nueva:

```bash
bash packaging/install-desktop.sh
```

`./mkscript-studio --check` comprueba las dependencias sin abrir la aplicación.

## Arquitectura

```text
src/mkscript_studio/
├── app.py, window.py          # Adw.Application, ventana, navegación, acciones y atajos
├── context.py                 # Servicios compartidos (sin GTK)
├── paths.py, persistence.py   # Rutas XDG, ~, JSON atómico y cuarentena de archivos dañados
├── preferences/               # Modelo validado + almacén con notificaciones
├── generator/                 # Modelos, motor determinista, plantillas y escapes Bash
├── editor/
│   ├── completion.py          # Motor de autocompletado y lógica de Tab (puro Python)
│   ├── bash_words.py          # Vocabulario con descripciones
│   └── code_editor.py         # GtkSourceView + lista de sugerencias + buscar/reemplazar
├── services/                  # Archivos, copias, validación, ejecución, procesos, proyectos, estado
├── ui/                        # Vistas: inicio, biblioteca, studio, proyectos, preferencias
└── resources/                 # CSS (paleta grafito/turquesa), esquemas del editor, icono
```

Todo lo que está fuera de `app.py`, `window.py`, `editor/code_editor.py` y `ui/` funciona sin GTK y
tiene pruebas.

### Decisiones técnicas

- **GTK 4 + libadwaita + GtkSourceView 5**: aplicación nativa de GNOME. GtkSourceView aporta
  resaltado, números de línea, indentación, deshacer y búsqueda mantenidos por GNOME, así que no hubo
  que escribir un editor desde cero.
- **Lista de sugerencias propia** (un `Gtk.Popover` que no roba el foco) en lugar de
  `GtkSourceCompletion`. El comportamiento de Tab que se pedía (completar directamente si hay una sola
  coincidencia, indentar si no hay ninguna) necesita control total del teclado. Además, la lógica vive
  en `completion.resolve_tab`, que es Python puro y tiene pruebas.
- **JSON** para preferencias, estado, proyectos y plantillas propias. Son pocos datos y así se pueden
  leer y editar a mano; SQLite no aportaba nada. Los archivos dañados se apartan
  (`*.corrupt-FECHA`) en lugar de borrarse.
- **Solo Bash**: el selector de shell muestra únicamente Bash y lo explica. Zsh y POSIX sh se añadirán
  cuando el motor sepa generar código correcto para ellos.
- **Paleta**: con libadwaita ≥ 1.6 se redefinen variables CSS y con versiones anteriores, colores con
  nombre. La paleta grafito solo se carga en modo oscuro.

## Empaquetado (fase posterior)

- **Flatpak** (recomendado): runtime `org.gnome.Platform` ≥ 46, que ya incluye GTK 4, libadwaita y
  GtkSourceView 5. Haría falta un manifiesto con módulo Python, `finish-args` de acceso a la carpeta
  personal (`--filesystem=home`) y la opción de ejecutar en el anfitrión con `flatpak-spawn --host`
  para que los scripts vean el sistema real.
- **Paquetes de distribución**: ya existe `pyproject.toml`, el lanzador `.desktop` y el icono SVG.
  Falta añadir un archivo AppStream `metainfo.xml`.

## Limitaciones conocidas

- **La interfaz no se ha ejecutado todavía.** Esta versión se escribió en un equipo Windows sin WSL ni
  GTK. El núcleo tiene 138 pruebas que pasan (con Bash de Git for Windows). Las 5 pruebas de permisos
  POSIX y enlaces simbólicos se omitieron allí. La interfaz se revisó con comprobación estática de
  tipos contra los stubs de PyGObject (nombres de métodos, propiedades y firmas), pero nadie la ha
  abierto todavía. Puede haber errores de presentación o de comportamiento que solo aparecen al
  ejecutarla.
- Editor de un solo documento (sin pestañas).
- El análisis de riesgos antes de ejecutar es aproximado (expresiones regulares). No es un análisis
  de seguridad.
- La ejecución no ofrece entrada interactiva (stdin cerrado). Los scripts que usan `read` o `sudo`
  sin terminal fallarán en esa orden.
- La copia de seguridad en Studio se hace una vez por documento y sesión, no en cada guardado.
- Sin perfiles de configuración. El modelo de preferencias es plano y versionado para poder añadirlos.
- Icono provisional, creado para este proyecto.
