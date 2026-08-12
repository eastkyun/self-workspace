# FullCalendar Standard 7.0.2

- Upstream release: https://github.com/fullcalendar/fullcalendar/releases/tag/v7.0.2
- Download: https://github.com/fullcalendar/fullcalendar/releases/download/v7.0.2/fullcalendar-7.0.2.zip
- ZIP SHA-256: `c6713f53cbf4824559884c410e45b671451a46bddd649b753c5f59ba0ee0f089`
- License: MIT (see `LICENSE.md`)
- Retrieved: 2026-07-28

Vendored files are copied unchanged from the official release ZIP:

- `dist/fullcalendar.global.js`
- `dist/locales-all/global.js`
- `dist/skeleton.css`
- `dist/themes/classic/global.js`
- `dist/themes/classic/theme.css`
- `dist/themes/classic/palette.css`
- `LICENSE.md`

## Application bundle

`daygrid.esm.js` is the locally served, minified ESM bundle used by the calendar.
It contains only the FullCalendar core, DayGrid, classic theme, and Korean locale,
plus their required runtime dependencies:

- `fullcalendar@7.0.2`
- `@full-ui/headless-calendar@7.0.2`
- `preact@10.29.1`
- `temporal-polyfill@1.0.1`

It was generated with `esbuild@0.25.9` using `--bundle --format=esm --minify
--target=es2022 --legal-comments=eof`. The source entry imports `Calendar`,
`fullcalendar/daygrid`, `fullcalendar/themes/classic`, and
`fullcalendar/locales/ko`, and exports those four values. Runtime code has no CDN
dependency. Dependency license texts are stored beside this file. Generated bundle
SHA-256: `02715ac3ec8286801d60183fb821ab4ace1964504cc18577bcdc9eef343e6c24`.

The application loads these files locally. It has no CDN or runtime network dependency.
