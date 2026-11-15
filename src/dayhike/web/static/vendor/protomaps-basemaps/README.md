# Protomaps basemap style assets

- `basemaps.js`: `@protomaps/basemaps` 5.7.2 (`dist/basemaps.js` from npm), BSD-3-Clause and CC0 (`LICENSE.md`).
- `fonts/` and `sprites/`: from https://github.com/protomaps/basemaps-assets at commit
  `028c18f713baecad011301ff7a69acc39bcc2ae7`. Fonts are Noto Sans under the SIL Open Font License (`OFL.txt`);
  sprites (`sprites/v4/light*`) are derived from Mapzen's icons under MIT (`sprites/LICENSE.md`).
- Only the glyph ranges for Latin, Latin Extended, IPA, Greek, Cyrillic and punctuation are kept
  (`0-255` to `1024-1279`, `7680-7935`, `8192-8447`): enough for European labels. A label with a character
  outside them is drawn without that character.
