# gen3tl - a translation toolkit for Gen 3 (GBA) Pokemon ROM hacks

<img src="web/brand.png" alt="gen3tl" width="160">

gen3tl is the toolkit and the website behind translations of Gen 3 (GBA) Pokemon ROM hacks. The website is https://haritos90.github.io/gen3tl/.

## Translation projects

Each game has its own translation project: a separate repository that holds the translations of that game, one folder per language. A released language is published as a patch for the original ROM of the game; no ROM is distributed.

- Pokemon Unbound: [haritos90/unbound-tl](https://github.com/haritos90/unbound-tl)

The website lists every game with its languages.

## Contribute to a translation

Translations are edited in the [translation editor](https://haritos90.github.io/gen3tl/editor). The editor works through a web browser, all work stays local until submitted. `Submit` in the editor opens a GitHub issue in the translation project of the game, a check turns the issue into a GitHub pull request, and a maintainer reviews it. The `CONTRIBUTING.md` of the translation project describes the steps.

## Add a language

Open an issue in the translation project of the game and name the language, or open a pull request there that adds the language. The language then appears in the editor and is translated as described above.

## Add a game

Open an [issue](https://github.com/haritos90/gen3tl/issues) in this repository and name the game. A game appears on the website once its translation project exists.

## This repository

| Part | Content |
| --- | --- |
| Toolkit | the command-line program that reads the texts of a game, checks the translations and builds the patch of a release |
| Web pages | the website: the translation editor, the patcher, the save converter and the Pokédex |
| Patcher | the page that applies a released translation, or any UPS, IPS, BPS, xdelta, APS or PPF patch, to a ROM; the files stay in the browser |

The website holds no game data. It reads the games, their languages and their releases from the translation projects when a page opens.

Changes to the toolkit and the pages follow [CONTRIBUTING.md](CONTRIBUTING.md).

## License

gen3tl is licensed under the MIT License, in [LICENSE](LICENSE). The web fonts in `web/fonts/` are bundled under their own license, the SIL Open Font License 1.1 ([web/fonts/OFL.txt](web/fonts/OFL.txt)).
