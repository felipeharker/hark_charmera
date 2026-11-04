# hark_charmera — agent guide

Charmera, https://charmera.felipeharker.org/: custom effects for the Kodak
Charmera keychain camera, patched into its stock firmware, and the one-page
site that documents them. Read [`README.md`](README.md) first, then
[`REPORT.md`](REPORT.md) for the reverse-engineering notes. The site is
built to the standard of the sites at felipeharker.org; the standard, the
decision log and the roadmap live in
[`hark_web`](https://github.com/felipeharker/hark_web) (`web/standards/`,
`planning/`).

## Rules

1. **`main` is live.** Render deploys every push to `main`. Work on a branch;
   push or merge to `main` only when the user asks. Never change Render
   settings unless asked.
2. **The repository is public.** Every file here can be read by anyone, not
   only what is served. No secrets, no `.env`, no private notes, no personal
   photos.
3. **No firmware in git.** Kodak's firmware — the public dumps in
   `third_party/`, built update packages (`*.bin`), full images, disassembly,
   frames and icons extracted from it — is third-party material and stays on
   disk, out of git (`.gitignore`). The repository holds only what is written
   here: the patcher, the C source, the tests, the docs and the site. Images
   on the site are this project's own output (emulator renders), never
   extracted stock artwork.
4. **No links to private `github.com/felipeharker/…` repositories.** The
   public `hark_` repositories may be linked.
5. **One stylesheet, and it is not here.** The page links
   `https://felipeharker.org/standards/hark.css`, served by `hark_web`, then
   `assets/site.css`, which holds only what this page adds, commented, in
   classes of its own. Never copy the standard. No inline `style`
   attributes, no CSS framework, no build step, no JavaScript of its own.
   Every page links `https://felipeharker.org/standards/hark.js` straight
   after its stylesheets.
6. **Only `site/` is published.** Working files go in `source/`, which git
   ignores.
7. **Names.** Anything served is a URL, so it is lowercase kebab-case
   (`img/view-text.png`).
8. **Links.** Within this site: relative. The running head and the site
   links are the Personal section's — Portfolio, the Ornithology Report,
   Dove Habitat and Charmera — in the same tab (`tools/site.mjs`, `NAV`).
   To anyone else: absolute, `target="_blank" rel="noopener noreferrer"`.
9. **Text.** Plain language; headings in Title Case, never numbered.
   Addresses, register values and file names are recorded values, in mono.
   The page opens with the running head and ends with the colophon; the
   standards link is `https://felipeharker.org/standards/`. Say plainly what
   has and has not been tested on a camera.
10. **The firmware build is reproducible.** A change to the patch is a
    change to `tools/build_fx.py`, `fx_text/` or `looks.json`, followed by
    `python tools/emu_test.py`, which must print `RESULT: ALL PASS`.
11. **Keep the docs true.** A change to how something works updates the
    README, REPORT.md or the page in the same commit. A decision goes in
    `hark_web`'s `planning/decisions.md`. The Personal section's links are
    in `tools/site.mjs` here and in `hark_birding`, `hark_dove_habitat` and
    `hark_web`: change them in all four.

## Before a commit

1. `node tools/check.mjs` passes.
2. If the firmware patch changed: `python tools/emu_test.py` passes.
3. `node tools/serve.mjs`, and open the page at desktop width and at 390 px.

Commit messages end with the attribution line the session gives, for example
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
