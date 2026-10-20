# BioShock 3D PC Port

[English](#english) · [Español](#español)

## English

> **Disclaimer:** `bio3dpc` is not a decompilation or a static recompilation of BioShock 3D. It is an effort to preserve the specific BREW copies for LG VX8350 v1.4.1.1 and Nokia 7705 v1.5.2.1 and make them playable on PC, currently targeting Linux x86_64. Unicorn executes each original ARM module, including its software renderer, while Python supplies BREW services, desktop controls, display and audio. You must supply your own matching modules and data. Full campaign playability is unverified; later Nokia campaign packs are missing.

![BioShock 3D — BREW PC PORT, with a Bathysphere overlooking Rapture](docs/media/banner.png)

Preserve and play **BioShock 3D**, the 2010 BREW mobile game, on a Linux PC. `bio3dpc` executes the original ARM code through Unicorn while Python supplies the BREW runtime, desktop controls, audio and a tkinter window.

**Bring your own game:** original BREW modules and data are not included or downloaded. The **LG VX8350 portrait** and **Nokia 7705 landscape** editions have separate profiles. Both reach gameplay; full campaign completion has not been verified, and the Nokia collection lacks later campaign packs.

[Gameplay](#gameplay) · [Quick start](#quick-start) · [Game files](#game-files) · [How the port works](#how-the-port-works) · [Controls](#controls) · [Development](#development)

### Gameplay

Actual framebuffer recordings, enlarged 2× with nearest-neighbor scaling. These silent loops show the game running, separately from generated launcher artwork.

| LG VX8350 · 176×220 portrait | Nokia 7705 · 320×240 landscape |
| --- | --- |
| ![Portrait gameplay with wrench and HUD](docs/media/gameplay-vx8350.gif) | ![Landscape opening scene with HUD](docs/media/gameplay-n7705.gif) |
| v1.4.1.1 · seven matching campaign packs required | v1.5.2.1 · only two campaign packs available |

See [build coverage](docs/builds.md) for the exact limits of each edition.

### Requirements

- **Linux x86_64**, Python 3 with **tkinter**, and GNU Make. Development has used Python 3.14.
- Python dependencies installed through `requirements.txt`, including Unicorn and Capstone.
- **FFmpeg** and **`paplay`** on `PATH` for sound effects and voices.
- **FluidSynth** and a **SoundFont** for MIDI music. Without a SoundFont, music remains silent but keeps its timing.

Place a `.sf2` file in `~/.local/share/soundfonts/` or set `BIO3DPC_SOUNDFONT`. Missing audio tools do not prevent silent play. See [audio details](docs/advanced-usage.md#midi).

### Quick start

Install Python, tkinter and Make, then:

```sh
git clone https://github.com/aurelioochoa/bio3dpc.git
cd bio3dpc
make venv
./bio3dpc.sh
```

Choose your edition, open **Installation**, select your matching BREW module and data folder, and choose **Verify files**. After successful verification, use **Save and return → Play**.

For an applications-menu entry and the `~/.local/bin/bio3dpc` command:

```sh
make install-desktop
```

Keep the checkout in place. `make uninstall-desktop` removes the entry and installed command while preserving preferences, saves and original files.

### Game files

Supply extracted **BioShock 3D BREW game files**, keeping their original names:

| Edition | Module and matching data |
| --- | --- |
| LG VX8350 · v1.4.1.1 | `bio3d.mod`, `bio3d.idx`, and `bio1.dat` through `bio7.dat` |
| Nokia 7705 · v1.5.2.1 | Matching `bio3d.mod`, `bio3d.idx`, `bio1.dat` and `bio2.dat`; later campaign packs are missing |

In **Installation**, select **Original BREW module (.mod)** and the **Matching game data folder (.dat + .idx)**. Choose the folder containing the actual data files, rather than its parent. Verification checks module and starter asset hashes and required pack names; it does not establish full campaign coverage. Files from other handset builds are not substitutes.

**Files are used in place:** the launcher remembers their paths without copying or modifying them. Keep the source folder or external drive available while playing. APK, IPA, JAR and console ROM formats are unsupported. See [build identities and coverage](docs/builds.md) and [detailed installation guidance](docs/advanced-usage.md#installation-bring-your-own-game-files).

### How the port works

The original BREW `.mod` contains the ARM game code, including its software 3D renderer. **Unicorn** executes that code while Python implements the phone services and desktop frontend it uses.

1. **Load the handset build.** A module hash selects its profile, screen dimensions and applet class ID. The loader copies the module into a **64 MiB** region shared between Unicorn and the host, below the 32-bit address limit. It supplies the BREW helper table and calls the module's original entry shim, which performs relocation.
2. **Recreate the BREW lifecycle.** The host calls `AEEMod_Load`, creates the `IApplet`, sends `EVT_APP_START`, then pumps scheduled timers and callbacks. BREW interfaces such as `IShell`, `IDisplay`, `IBitmap`, `IFileMgr` and `ISoundPlayer` have guest-visible method tables. Calls into those tables reach Python handlers through ARM trap stubs, then return to the game.
3. **Display the software framebuffer.** The emulated ARM game rasterizes the scene into a guest bitmap. The frontend converts its RGB565 pixels into a PPM image for **tkinter**, scaled from the handset's native **176×220** or **320×240** display. The supported profiles use this CPU rendering path, so rendering work also consumes emulated CPU time.
4. **Bridge input and audio.** Desktop keys become BREW key press/release events; verified builds apply relative mouse movement through the game's native camera methods. FFmpeg decodes QCP/QCELP sound and voices, FluidSynth renders MIDI when a SoundFont is available, and `paplay` plays the resulting audio. Host callbacks preserve playback timing, including silent MIDI operation without a SoundFont.
5. **Separate assets from saves.** The game reads its original `.idx` and `.dat` resources from the selected folder. Writable save files go to an overlay keyed by module SHA-256, keeping handset editions separate and source files unchanged. Build-specific hooks and service coverage limit compatibility; the missing Nokia campaign packs cannot be supplied by the runtime.

Source entry points: [module loading](bio3dpc/modfile.py), [ARM execution](bio3dpc/machine.py), [boot lifecycle](bio3dpc/boot.py), [BREW services](bio3dpc/services.py), [desktop frontend](bio3dpc/frontend.py), and [audio](bio3dpc/audio.py). See the [source layout](docs/advanced-usage.md#layout) and [build coverage](docs/builds.md) for more detail.

### Launcher

![Bathysphere launcher with edition selection and controls](docs/launcher-preview.png)

The Bathysphere launcher provides edition selection, installation verification, a window-scale slider, an audio switch and saved-game access. Its generated underwater artwork animates when FFmpeg is available; animation pauses during play and can be disabled in Settings.

Open **Controls** or **Settings → Controls** to rebind keyboard actions, enable mouse look, set sensitivity from **0.1× to 5.0×**, or invert vertical movement. Click a binding and press a new key; Backspace clears it and Escape cancels capture. **Reset controls** restores defaults; **Save and return** saves changes for future launcher and CLI sessions.

Tab navigates launcher controls, arrows adjust sliders and switches, and Enter or Space activates buttons. Narrow windows stack artwork and controls with scrolling. See [control settings](docs/advanced-usage.md#control-settings) and [launcher artwork and animation](docs/launcher-art.md).

### Controls

| Input | Action |
| --- | --- |
| W / S | Forward / backward; up / down in menus |
| A / D | Strafe left / right; left / right in menus |
| Mouse movement | Turn and look up/down |
| Left mouse | Fire weapon/plasmid; confirm in menus |
| Right mouse | Switch weapon/plasmid; back in menus |
| E | Use / pick up; confirm prompts |
| R / F | Reload or refill EVE / use first aid |
| 1–5 | Select an owned weapon |
| F1–F3 in gameplay | Select an owned plasmid |
| Mouse wheel | Cycle owned equipment in the current mode |
| Q / middle mouse | Cycle available ammunition |
| Enter / Space / keypad Enter | Phone OK / confirm |
| Escape / Backspace | Pause / back |
| Tab | Release / capture pointer |
| Numeric keypad 0–9 | Original phone number keys |
| F1–F4 in menus | Phone soft keys; F2 skips cutscenes |
| F10 / F11 | Phone soft keys 1 / 2; F11 also skips cutscenes |
| F5–F7 | Phone function keys 1–3 |
| Arrows | Phone directional pad; up/down also aim during gameplay |
| Home / End, `*` / `#` | Phone SEND / END, star / pound |

Equipment must be acquired before selection. The top number row selects equipment; use the numeric keypad for phone-number hints and hacking. Space sends phone OK, which the original help also labels as crouch. This mobile game has no separate bindings for jump, zoom, radial selection or quicksave/quickload.

Mouse look is enabled by default. Tab releases the captured pointer; clicking recaptures it without firing. Focus loss releases inputs. Menus support hover, click, right-click back and wheel navigation. Command-line overrides include `--no-mouse`, `--mouse-sens 2.0` and `--invert-y`.

For full binding rules, camera behavior and introductory phone prompts, see [the detailed controls reference](docs/advanced-usage.md#playing).

### Saves and settings

| Content | Default location |
| --- | --- |
| Original game files | Your selected module and data paths; kept read-only |
| Per-build save overlay | `~/.local/state/bio3dpc/saves/<module-sha256>/` |
| Launcher and control settings | `~/.config/bio3dpc/launcher.json` |
| Auto-detected SoundFonts | `~/.local/share/soundfonts/*.sf2` |
| Development captures and logs | `/tmp/opencode/bio3dpc` (`OUT` override) |

`XDG_STATE_HOME` and `XDG_CONFIG_HOME` override the corresponding save and settings bases. Portrait and landscape saves remain separate. The game writes `savedata.dat` to its overlay and offers **CONTINUE** on the next boot. Original files remain unchanged.

### Status and limitations

Both supported profiles reach first-person gameplay. Observed checks include startup, introduction, menus, keyboard input, persistent saves, QCP/QCELP sound decoding and optional MIDI rendering. Full campaign completion is unverified. **The Nokia collection only has its first two campaign packs.**

Earlier VX8350 measurements recorded roughly 19 gameplay ticks/s (about 38 fps); the emulated CPU performs the game's software rendering. Those are historical measurements, not a performance guarantee. Additional handset builds are inventoried but not qualified; LG VX10000 also needs EGL/GLES support. Some BREW classes remain unimplemented or refused.

See [coverage and selection rationale](docs/builds.md), [observed runtime results](docs/advanced-usage.md#status), and [research notes](docs/research.md).

### Development

Contributions use this repository's GitHub issues and pull requests. Read the bilingual [contribution guide](CONTRIBUTING.md#english) and [piracy policy](CODE_OF_CONDUCT.md#english).

| Command | Purpose |
| --- | --- |
| `make` | List available targets |
| `make venv` | Create the virtual environment and install dependencies |
| `make launcher` | Open the launcher |
| `make play` / `make landscape` | Launch portrait / landscape gameplay |
| `make test` | Run tests without game data |
| `make smoke` / `make mouse` | Check window/input / mouse behavior with game files |
| `make input` | Exercise real gameplay input with isolated saves |
| `make shot SECS=30` | Capture a frame after 30 seconds |
| `make clean` | Remove development caches |

Override paths with `make play GAME_DIR="/path/to/build" DATA="/path/to/data"` or `BIO3DPC_GAME_DIR` and `BIO3DPC_DATA_DIR`. Standalone tools use `.venv/bin/python tools/play.py --mod "/path/to/bio3d.mod" --data-dir "/path/to/data"`.

Before publishing, stage intended files and run `python3 tools/check_publish.py`; `python3 tools/check_publish.py --media` validates README media. CI runs the audit and tests without original files. See [publishing notes](docs/publishing.md), [detailed tools and source layout](docs/advanced-usage.md), and [reverse-engineering workflow](docs/rea-workflow.md).

### License

The host's original source is licensed under [MIT](LICENSE). This excludes original game binaries, assets, trademarks and third-party dependencies.

BioShock and BioShock 3D belong to their respective rights holders. This unofficial preservation project has no affiliation or endorsement. Original modules, data, SDK headers, extracted sounds and fonts are not distributed here. Unicorn, Pillow and other dependencies retain their licenses.

README gameplay media contains rendered captures. Banner and launcher artwork are generated; see [banner provenance](docs/banner-art.md) and [launcher provenance](docs/launcher-art.md).

### Credits

- **Studio Tridev (IG Fun) and the original game's publishers and rights holders:** BioShock 3D, its ARM engine, software renderer, artwork and audio; see the [documented game history](docs/research.md).
- **Unicorn contributors:** execution of the original BREW ARM modules.
- **Python and Tcl/Tk contributors:** the BREW service implementations' host language and desktop gameplay window. **Pillow contributors:** launcher artwork scaling. **Capstone contributors:** development disassembly tools.
- **FFmpeg, FluidSynth and PulseAudio contributors:** original sound/voice decoding, optional MIDI synthesis and audio playback through `paplay`. SoundFont creators retain credit and license terms for the separate fonts supplied by users.
- **Project contributors and testers:** handset profiles, BREW compatibility services, PC controls, launcher and preservation documentation. See [banner provenance](docs/banner-art.md) and [launcher artwork credits](docs/launcher-art.md) for visual assets.

Third-party components retain their own licenses. These acknowledgments do not imply affiliation with or endorsement by the original game's creators or rights holders.

## Español

> **Aviso:** `bio3dpc` no es una descompilación ni una recompilación estática de BioShock 3D. Es un esfuerzo para preservar las copias específicas de BREW para LG VX8350 v1.4.1.1 y Nokia 7705 v1.5.2.1 y permitir jugarlas en PC, actualmente en Linux x86_64. Unicorn ejecuta cada módulo ARM original, incluido su renderizador por software, mientras Python proporciona servicios de BREW, controles de escritorio, pantalla y audio. Debes aportar tus propios módulos y datos correspondientes. La jugabilidad de toda la campaña sigue sin verificarse; faltan los paquetes posteriores de Nokia.

![BioShock 3D: port de BREW para PC, con una batisfera frente a Rapture](docs/media/banner.png)

Preserva y juega **BioShock 3D**, el juego móvil de BREW de 2010, en un PC con Linux. `bio3dpc` ejecuta el código ARM original mediante Unicorn; Python proporciona el runtime de BREW, los controles de escritorio, el audio y una ventana de tkinter.

**Debes aportar tu propio juego:** los módulos y datos originales de BREW no se incluyen ni se descargan. Las ediciones **LG VX8350 vertical** y **Nokia 7705 horizontal** tienen perfiles separados. Ambas llegan al juego; no se ha verificado la campaña completa y la colección de Nokia carece de los paquetes posteriores.

[Jugabilidad](#jugabilidad) · [Inicio rápido](#inicio-rápido) · [Archivos del juego](#archivos-del-juego) · [Cómo funciona el port](#cómo-funciona-el-port) · [Controles](#controles) · [Desarrollo](#desarrollo)

### Jugabilidad

Grabaciones reales del framebuffer, ampliadas al doble con escalado por vecino más cercano. Estos bucles sin sonido muestran el juego en ejecución; el arte generado del lanzador es independiente.

| LG VX8350 · 176×220 vertical | Nokia 7705 · 320×240 horizontal |
| --- | --- |
| ![Juego vertical con llave inglesa e indicadores](docs/media/gameplay-vx8350.gif) | ![Escena inicial horizontal con indicadores](docs/media/gameplay-n7705.gif) |
| v1.4.1.1 · requiere siete paquetes de campaña compatibles | v1.5.2.1 · solo hay dos paquetes de campaña disponibles |

Consulta la [cobertura de versiones](docs/builds.md) (en inglés) para conocer los límites de cada edición.

### Requisitos

- **Linux x86_64**, Python 3 con **tkinter** y GNU Make. El desarrollo ha utilizado Python 3.14.
- Dependencias de Python instaladas desde `requirements.txt`, incluidas Unicorn y Capstone.
- **FFmpeg** y **`paplay`** en `PATH` para efectos de sonido y voces.
- **FluidSynth** y un **SoundFont** para la música MIDI. Sin SoundFont, la música permanece en silencio, pero mantiene su duración.

Coloca un archivo `.sf2` en `~/.local/share/soundfonts/` o define `BIO3DPC_SOUNDFONT`. La falta de herramientas de audio permite seguir jugando sin sonido. Consulta los [detalles de audio](docs/advanced-usage.md#midi) (en inglés).

### Inicio rápido

Instala Python, tkinter y Make; después ejecuta:

```sh
git clone https://github.com/aurelioochoa/bio3dpc.git
cd bio3dpc
make venv
./bio3dpc.sh
```

Elige la edición, abre **Installation**, selecciona el módulo de BREW y su carpeta de datos compatibles, y pulsa **Verify files**. Tras verificar, usa **Save and return → Play**.

Para instalar la entrada del menú de aplicaciones y el comando `~/.local/bin/bio3dpc`:

```sh
make install-desktop
```

Conserva el repositorio en su ubicación. `make uninstall-desktop` elimina la entrada y el comando instalado; conserva las preferencias, partidas y archivos originales.

### Archivos del juego

Aporta los **archivos extraídos de BioShock 3D para BREW** y conserva sus nombres originales:

| Edición | Módulo y datos compatibles |
| --- | --- |
| LG VX8350 · v1.4.1.1 | `bio3d.mod`, `bio3d.idx` y `bio1.dat` hasta `bio7.dat` |
| Nokia 7705 · v1.5.2.1 | `bio3d.mod`, `bio3d.idx`, `bio1.dat` y `bio2.dat` compatibles; faltan los paquetes posteriores |

En **Installation**, selecciona **Original BREW module (.mod)** y **Matching game data folder (.dat + .idx)**. Elige la carpeta que contiene los datos, no su carpeta superior. La verificación comprueba los hashes del módulo y de los recursos iniciales, además de los nombres de paquetes requeridos; no demuestra cobertura de toda la campaña. Los archivos de otros teléfonos no son sustitutos compatibles.

**Los archivos se usan en su ubicación original:** el lanzador recuerda sus rutas sin copiarlos ni modificarlos. Mantén disponible la carpeta o unidad externa durante el juego. No se admiten APK, IPA, JAR ni ROM de consola. Consulta las [identidades y cobertura de versiones](docs/builds.md) y la [guía detallada de instalación](docs/advanced-usage.md#installation-bring-your-own-game-files) (en inglés).

### Cómo funciona el port

El `.mod` original de BREW contiene el código ARM del juego, incluido su renderizador 3D por software. **Unicorn** ejecuta ese código y Python implementa los servicios del teléfono y la interfaz de escritorio que utiliza.

1. **Cargar la versión del teléfono.** El hash del módulo selecciona su perfil, dimensiones de pantalla e identificador de clase del applet. El cargador copia el módulo en una región de **64 MiB** compartida entre Unicorn y el host, bajo el límite de direcciones de 32 bits. Proporciona la tabla de funciones auxiliares de BREW y llama a la rutina de entrada original del módulo, que realiza la reubicación.
2. **Recrear el ciclo de vida de BREW.** El host llama a `AEEMod_Load`, crea el `IApplet`, envía `EVT_APP_START` y atiende temporizadores y callbacks programados. Las interfaces de BREW, como `IShell`, `IDisplay`, `IBitmap`, `IFileMgr` e `ISoundPlayer`, tienen tablas de métodos visibles para el código emulado. Sus llamadas llegan a funciones Python mediante pequeñas rutinas ARM de enlace y después regresan al juego.
3. **Mostrar el framebuffer por software.** El juego ARM emulado rasteriza la escena en un mapa de bits de su memoria. La interfaz convierte sus píxeles RGB565 en una imagen PPM para **tkinter**, escalada desde los **176×220** o **320×240** originales. Los perfiles admitidos usan este renderizado por CPU, por lo que dibujar también consume tiempo de CPU emulada.
4. **Conectar entrada y audio.** Las teclas del escritorio se convierten en eventos de pulsación y liberación de BREW; las versiones verificadas aplican el movimiento relativo del ratón mediante los métodos de cámara originales. FFmpeg decodifica sonidos y voces QCP/QCELP, FluidSynth renderiza MIDI cuando hay un SoundFont y `paplay` reproduce el audio resultante. Los callbacks del host conservan la temporización de reproducción, incluido el MIDI silencioso sin SoundFont.
5. **Separar recursos y partidas.** El juego lee sus recursos originales `.idx` y `.dat` de la carpeta seleccionada. Los archivos de partidas se escriben en una capa separada identificada por el SHA-256 del módulo, aislando las ediciones y conservando los originales. Los enlaces específicos de cada versión y los servicios implementados limitan la compatibilidad; el runtime no puede suplir los paquetes de campaña ausentes de Nokia.

Puntos de entrada del código: [carga de módulos](bio3dpc/modfile.py), [ejecución ARM](bio3dpc/machine.py), [ciclo de inicio](bio3dpc/boot.py), [servicios BREW](bio3dpc/services.py), [interfaz de escritorio](bio3dpc/frontend.py) y [audio](bio3dpc/audio.py). Consulta la [estructura del código](docs/advanced-usage.md#layout) y la [cobertura por versión](docs/builds.md) para más detalles (en inglés).

### Lanzador

![Lanzador de batisfera con selección de edición y controles](docs/launcher-preview.png)

El lanzador de batisfera permite elegir edición, verificar la instalación, cambiar la escala de ventana, activar el audio y acceder a las partidas. Su arte submarino generado se anima cuando FFmpeg está disponible; la animación se pausa durante el juego y puede desactivarse en Settings.

Abre **Controls** o **Settings → Controls** para reasignar acciones del teclado, activar la vista con ratón, ajustar la sensibilidad entre **0,1× y 5,0×** o invertir el eje vertical. Pulsa una asignación y una nueva tecla; Backspace la borra y Escape cancela. **Reset controls** restaura los valores predeterminados; **Save and return** guarda los cambios para futuros inicios desde el lanzador o la terminal.

Tab recorre los controles, las flechas ajustan deslizadores e interruptores, y Enter o Espacio activan botones. Las ventanas estrechas apilan el arte y los controles con desplazamiento. Consulta los [ajustes de controles](docs/advanced-usage.md#control-settings) y el [arte y animación del lanzador](docs/launcher-art.md) (en inglés).

### Controles

| Entrada | Acción |
| --- | --- |
| W / S | Avanzar / retroceder; subir / bajar en menús |
| A / D | Desplazarse lateralmente; izquierda / derecha en menús |
| Mover el ratón | Girar y mirar arriba/abajo |
| Botón izquierdo | Disparar arma/plásmido; confirmar en menús |
| Botón derecho | Alternar arma/plásmido; volver en menús |
| E | Usar / recoger; confirmar indicaciones |
| R / F | Recargar o reponer EVE / usar primeros auxilios |
| 1–5 | Seleccionar un arma obtenida |
| F1–F3 durante el juego | Seleccionar un plásmido obtenido |
| Rueda del ratón | Recorrer el equipo obtenido del modo actual |
| Q / botón central | Cambiar el tipo de munición disponible |
| Enter / Espacio / Enter numérico | OK del teléfono / confirmar |
| Escape / Backspace | Pausa / volver |
| Tab | Liberar / capturar el puntero |
| Teclado numérico 0–9 | Teclas numéricas originales del teléfono |
| F1–F4 en menús | Teclas contextuales del teléfono; F2 omite cinemáticas |
| F10 / F11 | Teclas contextuales 1 / 2; F11 también omite cinemáticas |
| F5–F7 | Teclas de función 1–3 del teléfono |
| Flechas | Cruceta del teléfono; arriba/abajo también apuntan durante el juego |
| Home / End, `*` / `#` | SEND / END, asterisco / almohadilla del teléfono |

Debes obtener el equipo antes de seleccionarlo. La fila numérica superior selecciona equipo; utiliza el teclado numérico para las pistas del teléfono y el hackeo. Espacio envía OK, que la ayuda original también identifica como agacharse. Esta versión móvil no tiene asignaciones separadas para saltar, zoom, selección radial ni guardado/carga rápidos.

La vista con ratón está activada por defecto. Tab libera el puntero capturado; un clic lo recupera sin disparar. Perder el foco libera las entradas. Los menús permiten señalar, hacer clic, volver con el botón derecho y navegar con la rueda. Las opciones de terminal incluyen `--no-mouse`, `--mouse-sens 2.0` y `--invert-y`.

Consulta las reglas de asignación, la cámara y las indicaciones del teléfono en la [referencia detallada de controles](docs/advanced-usage.md#playing) (en inglés).

### Partidas y ajustes

| Contenido | Ubicación predeterminada |
| --- | --- |
| Archivos originales | Rutas del módulo y datos seleccionados; se mantienen de solo lectura |
| Capa de partidas por versión | `~/.local/state/bio3dpc/saves/<module-sha256>/` |
| Ajustes del lanzador y controles | `~/.config/bio3dpc/launcher.json` |
| SoundFonts detectados automáticamente | `~/.local/share/soundfonts/*.sf2` |
| Capturas y registros de desarrollo | `/tmp/opencode/bio3dpc` (variable `OUT`) |

`XDG_STATE_HOME` y `XDG_CONFIG_HOME` sustituyen las bases de partidas y ajustes. Las partidas de las ediciones vertical y horizontal permanecen separadas. El juego escribe `savedata.dat` en su capa de partidas y ofrece **CONTINUE** al volver a iniciarse. Los originales no se modifican.

### Estado y limitaciones

Ambos perfiles admitidos llegan al juego en primera persona. Las comprobaciones observadas incluyen inicio, introducción, menús, teclado, partidas persistentes, decodificación QCP/QCELP y música MIDI opcional. La campaña completa no está verificada. **La colección de Nokia solo contiene sus dos primeros paquetes de campaña.**

Las mediciones anteriores de VX8350 registraron aproximadamente 19 ticks/s durante el juego (unos 38 fps); la CPU emulada ejecuta el renderizado por software del juego. Son mediciones históricas, no una garantía de rendimiento. Hay otras versiones inventariadas, pero no validadas; LG VX10000 también necesita EGL/GLES. Algunas clases de BREW no están implementadas o se rechazan.

Consulta la [cobertura y selección de versiones](docs/builds.md), los [resultados observados](docs/advanced-usage.md#status) y las [notas de investigación](docs/research.md) (en inglés).

### Desarrollo

Las contribuciones se revisan en los issues y pull requests de este repositorio. Lee la [guía de contribución](CONTRIBUTING.md#español) y la [política de piratería](CODE_OF_CONDUCT.md#español); ambos documentos contienen inglés y español.

| Comando | Función |
| --- | --- |
| `make` | Mostrar los objetivos disponibles |
| `make venv` | Crear el entorno virtual e instalar dependencias |
| `make launcher` | Abrir el lanzador |
| `make play` / `make landscape` | Iniciar el juego vertical / horizontal |
| `make test` | Ejecutar pruebas sin datos del juego |
| `make smoke` / `make mouse` | Comprobar ventana/entrada y ratón con el juego |
| `make input` | Probar controles reales con partidas aisladas |
| `make shot SECS=30` | Capturar una imagen a los 30 segundos |
| `make clean` | Eliminar cachés de desarrollo |

Indica otras rutas con `make play GAME_DIR="/ruta/a/version" DATA="/ruta/a/datos"` o con `BIO3DPC_GAME_DIR` y `BIO3DPC_DATA_DIR`. Para usar la herramienta directamente: `.venv/bin/python tools/play.py --mod "/ruta/a/bio3d.mod" --data-dir "/ruta/a/datos"`.

Antes de publicar, añade los archivos previstos al índice y ejecuta `python3 tools/check_publish.py`; `python3 tools/check_publish.py --media` valida el material visual del README. CI ejecuta la auditoría y las pruebas sin archivos originales. Consulta las [notas de publicación](docs/publishing.md), las [herramientas y estructura del código](docs/advanced-usage.md) y el [flujo de ingeniería inversa](docs/rea-workflow.md) (en inglés).

### Licencia

El código original del host tiene licencia [MIT](LICENSE). Esto excluye binarios originales del juego, recursos, marcas y dependencias de terceros.

BioShock y BioShock 3D pertenecen a sus respectivos titulares. Este proyecto de preservación no es oficial ni cuenta con afiliación o respaldo. No se distribuyen módulos originales, datos, cabeceras del SDK, sonidos extraídos ni fuentes. Unicorn, Pillow y las demás dependencias conservan sus licencias.

El material del juego del README son capturas renderizadas. El banner y el arte del lanzador se generaron; consulta la [procedencia del banner](docs/banner-art.md) y del [lanzador](docs/launcher-art.md) (en inglés).

### Créditos

- **Studio Tridev (IG Fun), los editores y los titulares del juego original:** BioShock 3D, su motor ARM, renderizador por software, arte y audio; consulta la [historia documentada del juego](docs/research.md) (en inglés).
- **Colaboradores de Unicorn:** ejecución de los módulos ARM originales de BREW.
- **Colaboradores de Python y Tcl/Tk:** lenguaje anfitrión de las implementaciones de servicios de BREW y ventana de juego de escritorio. **Colaboradores de Pillow:** escalado del arte del lanzador. **Colaboradores de Capstone:** herramientas de desensamblado para desarrollo.
- **Colaboradores de FFmpeg, FluidSynth y PulseAudio:** decodificación de sonidos y voces originales, síntesis MIDI opcional y reproducción mediante `paplay`. Los creadores de SoundFonts conservan sus créditos y condiciones de licencia para las fuentes de sonido aportadas por los usuarios.
- **Colaboradores y testers del proyecto:** perfiles de teléfonos, servicios de compatibilidad BREW, controles de PC, lanzador y documentación de preservación. Consulta la [procedencia del banner](docs/banner-art.md) y los [créditos del arte del lanzador](docs/launcher-art.md) (en inglés).

Los componentes de terceros conservan sus propias licencias. Estos agradecimientos no implican afiliación ni respaldo de los creadores o titulares del juego original.
