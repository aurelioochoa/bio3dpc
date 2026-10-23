# Contributing to bio3dpc / Contribuir a bio3dpc

[English](#english) · [Español](#español)

## English

`bio3dpc` is an experimental Linux PC port of the BREW mobile game BioShock 3D. Python supplies the runtime around the original ARM code executed by Unicorn. Reports and changes are reviewed through this repository's [issues](https://github.com/aurelioochoa/bio3dpc/issues) and [pull requests](https://github.com/aurelioochoa/bio3dpc/pulls).

### Scope

The current target is **Linux x86_64**, with separate profiles for **LG VX8350 v1.4.1.1** and **Nokia 7705 v1.5.2.1**. Runtime, launcher, controls, audio, saves, documentation and test improvements are useful. Both profiles reach gameplay. Nokia's missing later packs are rebuilt from sibling VX9900 data, and all seven landscape levels load; a complete playthrough remains unverified.

Read the [README](README.md), [build coverage](docs/builds.md) and [detailed usage and architecture](docs/advanced-usage.md) before reporting a problem. Other handset builds are not interchangeable with the supported profiles. General BREW compatibility is outside the current scope.

### Reports and proposals

Open an [issue](https://github.com/aurelioochoa/bio3dpc/issues/new) in English or Spanish and include:

- Your revision (`git rev-parse HEAD`), Linux distribution, architecture and Python version.
- Handset profile, game version and the launcher's **Verify files** result.
- Reproduction steps, expected and observed results, and whether an earlier revision worked.
- Relevant logs and audio/display details when applicable. Remove private details; do not attach original modules, data, SDK headers or extracted resources.

For larger proposals, explain the problem and intended design. Distinguish synthetic tests from real gameplay observations; reaching a menu or opening scene does not establish full campaign compatibility.

### Development setup

Fork the repository, clone your fork and create a branch from `main`. Follow the [README requirements](README.md#requirements), then:

```sh
make venv
make test
```

Synthetic tests need no game files. Real-game checks need your own matching BREW module and data, tkinter and a graphical desktop. FFmpeg and `paplay` provide sound effects and voices; MIDI music also needs FluidSynth and a separately supplied SoundFont.

### Changes and verification

Keep each pull request focused on one problem and follow the existing Python style. Add tests when they catch a meaningful regression. Preserve original files, settings and saves; use synthetic fixtures or isolated save locations for checks. Keep portrait and landscape profiles separate.

| Command | Purpose |
| --- | --- |
| `make test` | Run tests without original game files |
| `make smoke` | Check window creation, rendering and input with game files |
| `make mouse` | Check pointer capture, mouse look and buttons with game files |
| `make input` | Exercise gameplay input with isolated saves |
| `python3 tools/check_publish.py` | Audit staged files for game data, credentials and local artifacts |
| `python3 tools/check_publish.py --media` | Validate README images and gameplay media; requires Pillow |

Run `make test` for code changes and the relevant real-game checks when files and a desktop are available. Select the matching build with `GAME_DIR` and `DATA` as described in the [development reference](README.md#development). For documentation, check links, examples and formatting; validate media when it changes.

Before publishing, stage only reviewed files and run the publication audit. It inspects the Git index, so unstaged edits are not covered. Follow the [publishing notes](docs/publishing.md) and [reverse-engineering workflow](docs/rea-workflow.md). State what you checked and identify checks you could not run.

### Pull requests

Target **`aurelioochoa/bio3dpc:main`**. Describe the problem, resulting behavior, affected profiles, verification and remaining limitations or regressions. Include screenshots when they help review visual changes. Do not include original game files or unrelated local artifacts.

### AI assistance

AI-assisted contributions are allowed. Contributors remain responsible for understanding and reviewing the content, verifying its behavior, and checking its provenance and license compatibility. Describe substantial AI assistance in the pull request.

### Provenance and licensing

Contribute only material you have the right to share. Prefer public documentation, reproducible observations and original work. Do not include copied proprietary code, leaked material, BREW modules or data, SDK headers, extracted audio or fonts. Follow the [piracy policy](CODE_OF_CONDUCT.md#english).

Original host source is covered by [MIT](LICENSE). Preserve copyright and license notices and dependency attribution. Original game binaries, assets and trademarks are excluded; third-party dependencies retain their own licenses. Gameplay captures retain the rights of their respective owners; follow the documented [media provenance](docs/publishing.md).

## Español

`bio3dpc` es un port experimental para Linux del juego móvil BioShock 3D de BREW. Python proporciona el runtime alrededor del código ARM original ejecutado por Unicorn. Los informes y cambios se revisan en los [issues](https://github.com/aurelioochoa/bio3dpc/issues) y [pull requests](https://github.com/aurelioochoa/bio3dpc/pulls) de este repositorio.

### Alcance

El objetivo actual es **Linux x86_64**, con perfiles separados para **LG VX8350 v1.4.1.1** y **Nokia 7705 v1.5.2.1**. Son útiles las mejoras del runtime, lanzador, controles, audio, partidas, documentación y pruebas. Ambos perfiles llegan al juego. Los paquetes posteriores que faltan en Nokia se reconstruyen con datos de VX9900, y los siete niveles horizontales cargan; una partida completa sigue sin verificarse.

Consulta el [README](README.md#español), la [cobertura de versiones](docs/builds.md) y el [uso detallado y arquitectura](docs/advanced-usage.md) antes de informar de un problema. Las dos últimas referencias están en inglés. Las versiones de otros teléfonos no sustituyen a los perfiles compatibles. La compatibilidad general con BREW queda fuera del alcance actual.

### Informes y propuestas

Abre un [issue](https://github.com/aurelioochoa/bio3dpc/issues/new) en inglés o español e incluye:

- Tu revisión (`git rev-parse HEAD`), distribución Linux, arquitectura y versión de Python.
- Perfil del teléfono, versión del juego y resultado de **Verify files** en el lanzador.
- Pasos para reproducirlo, resultado esperado y observado, y si funcionaba en una revisión anterior.
- Registros pertinentes y detalles de audio/pantalla cuando corresponda. Elimina datos privados; no adjuntes módulos originales, datos, cabeceras del SDK ni recursos extraídos.

Para propuestas amplias, explica el problema y el diseño previsto. Distingue las pruebas sintéticas de las observaciones reales del juego; llegar al menú o a la escena inicial no demuestra compatibilidad con toda la campaña.

### Preparar el entorno

Crea un fork, clónalo y abre una rama a partir de `main`. Sigue los [requisitos del README](README.md#requisitos) y ejecuta:

```sh
make venv
make test
```

Las pruebas sintéticas no necesitan el juego. Las pruebas reales requieren tu propio módulo BREW con sus datos correspondientes, tkinter y un escritorio gráfico. FFmpeg y `paplay` proporcionan efectos y voces; la música MIDI también necesita FluidSynth y un SoundFont aportado por separado.

### Cambios y pruebas

Mantén cada pull request centrado en un problema y respeta el estilo Python existente. Añade pruebas cuando detecten una regresión significativa. Conserva originales, ajustes y partidas; usa datos sintéticos o ubicaciones de guardado aisladas. Mantén separados los perfiles vertical y horizontal.

| Comando | Función |
| --- | --- |
| `make test` | Ejecutar pruebas sin archivos originales del juego |
| `make smoke` | Comprobar ventana, renderizado y entrada con el juego |
| `make mouse` | Comprobar captura del cursor, cámara y botones con el juego |
| `make input` | Probar entradas durante el juego con partidas aisladas |
| `python3 tools/check_publish.py` | Auditar archivos del índice para detectar datos del juego, credenciales y archivos locales |
| `python3 tools/check_publish.py --media` | Validar imágenes y material de juego del README; requiere Pillow |

Ejecuta `make test` para cambios de código y las comprobaciones reales pertinentes cuando dispongas del juego y un escritorio. Selecciona la versión correspondiente mediante `GAME_DIR` y `DATA`, como indica la [referencia de desarrollo](README.md#desarrollo). Para documentación, comprueba enlaces, ejemplos y formato; valida el material visual si cambia.

Antes de publicar, añade al índice solo los archivos revisados y ejecuta la auditoría de publicación. Esta inspecciona el índice de Git, por lo que no cubre cambios sin añadir. Sigue las [notas de publicación](docs/publishing.md) y el [flujo de ingeniería inversa](docs/rea-workflow.md) (en inglés). Indica qué comprobaste y qué pruebas no pudiste ejecutar.

### Pull requests

Envía el pull request a **`aurelioochoa/bio3dpc:main`**. Describe el problema, el comportamiento resultante, los perfiles afectados, las pruebas y cualquier límite o regresión pendiente. Incluye capturas si ayudan a revisar cambios visuales. No incluyas archivos originales del juego ni archivos locales ajenos al cambio.

### Uso de IA

Se permiten contribuciones asistidas por IA. Quien contribuye sigue siendo responsable de comprender y revisar el contenido, comprobar su funcionamiento y verificar su procedencia y compatibilidad de licencia. Describe en el pull request el uso sustancial de IA.

### Procedencia y licencias

Aporta únicamente material que puedas compartir. Prioriza documentación pública, observaciones reproducibles y trabajo original. No incluyas código propietario copiado, material filtrado, módulos o datos BREW, cabeceras del SDK, audio extraído ni fuentes. Cumple la [política de piratería](CODE_OF_CONDUCT.md#español).

El código original del host está cubierto por [MIT](LICENSE). Conserva los avisos de derechos de autor, licencias y atribución de dependencias. Se excluyen binarios, recursos y marcas del juego original; las dependencias mantienen sus propias licencias. Las capturas del juego conservan los derechos de sus respectivos titulares; sigue la [procedencia documentada del material visual](docs/publishing.md) (en inglés).
