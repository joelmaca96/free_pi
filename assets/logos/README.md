# Escudos de equipos

Escudos de los equipos de la **Liga Endesa (ACB)** y la **EuroLeague**,
descargados de Wikipedia/Wikimedia Commons.

Cada archivo está nombrado con el **slug** del equipo (el mismo que usa
`config.TEAMS` / la tabla `teams.slug`):

```
assets/logos/vitoria.png     # Baskonia
assets/logos/bilbao.svg      # Bilbao Basket
assets/logos/real-madrid.png # Real Madrid
```

## Equipos incluidos

### Liga Endesa (ACB)
| Slug | Equipo | Archivo |
|---|---|---|
| `vitoria` | Baskonia | `vitoria.jpg` |
| `bilbao` | Bilbao Basket | `bilbao.svg` |
| `canarias` | La Laguna Tenerife | `canarias.svg` |
| `zaragoza` | Casademont Zaragoza | `zaragoza.png` |
| `fundacion-granada` | Covirán Granada | `fundacion-granada.png` |
| `murcia` | UCAM Murcia | `murcia.svg` |
| `forca-lleida` | Hiopos Lleida | `forca-lleida.png` |
| `gran-canaria` | Dreamland Gran Canaria | `gran-canaria.svg` |
| `unicaja-malaga` | Unicaja | `unicaja-malaga.png` |
| `miraflores` | Recoletas Salud San Pablo Burgos | `miraflores.svg` |
| `breogan` | Río Breogán | `breogan.svg` |
| `andorra` | MoraBanc Andorra | `andorra.svg` |
| `basquet-girona` | Bàsquet Girona | `basquet-girona.svg` |
| `manresa` | Baxi Manresa | `manresa.png` |
| `joventut` | Joventut | `joventut.png` |
| `valencia` | Valencia Basket | `valencia.svg` |
| `barcelona` | Barcelona | `barcelona.svg` |
| `real-madrid` | Real Madrid | `real-madrid.png` |

### EuroLeague
| Slug | Equipo | Archivo |
|---|---|---|
| `olympiakos` | Olympiacos | `olympiakos.svg` |
| `villeurbanne` | LDLC ASVEL | `villeurbanne.svg` |
| `panathinaikos` | Panathinaikos AKTOR | `panathinaikos.svg` |
| `paris-basket` | Paris Basketball | `paris-basket.svg` |
| `partizan` | Partizan Mozzart Bet | `partizan.png` |
| `red-star` | Crvena zvezda Meridianbet | `red-star.svg` |
| `dubai` | Dubai | `dubai.svg` |
| `anadolu-efes` | Anadolu Efes | `anadolu-efes.svg` |
| `virtus-bologna` | Virtus Olidata Bologna | `virtus-bologna.svg` |
| `hapoel-tel-aviv` | Hapoel IBI Tel Aviv | `hapoel-tel-aviv.svg` |
| `maccabi-tel-aviv` | Maccabi Rapyd Tel Aviv | `maccabi-tel-aviv.svg` |
| `bayern-muenchen` | Bayern München | `bayern-muenchen.svg` |
| `zalgiris` | Žalgiris | `zalgiris.svg` |
| `milano` | EA7 Emporio Armani Milano | `milano.png` |
| `monaco` | AS Monaco | `monaco.png` |
| `ulker-fenerbahce` | Fenerbahçe Beko | `ulker-fenerbahce.svg` |

## Formato

Formatos admitidos: `.png`, `.jpg`, `.jpeg`, `.svg`. Si no existe una imagen
para un equipo, la GUI (`app.py`) muestra un icono de baloncesto genérico
como respaldo.

## Regenerar

Para volver a descargar los escudos (por ejemplo, si se añade un equipo nuevo):

```bash
python download_logos.py
```

El script usa la API de Wikipedia para localizar y descargar cada escudo.
Respeta el rate-limit de Wikimedia (espera entre peticiones).

## Nota legal

Los escudos son propiedad de cada club. Se incluyen aquí únicamente para uso
interno de scouting/análisis. No redistribuir sin licencia.
