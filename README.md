# cinemacity-watchdog

Hlídá rozpis [Cinema City](https://www.cinemacity.cz) a když přibude nový termín
**Odyssei v IMAXu** — nebo se u už známého termínu **uvolní místo v zadních
řadách** — založí v tomhle repu issue a **přiřadí ho vlastníkovi repa**.
GitHub z něj pošle e-mail i push do mobilní appky.

Na přiřazení záleží: e-mail chodí ve výchozím nastavení jen u „Participating"
notifikací (přiřazení, zmínky, odpovědi). Pouhé sledování repa („Watching")
dává jen web/mobile notifikaci — e-mail je pro něj v Settings → Notifications
vypnutý, dokud si ho člověk nezapne.

Běží v GitHub Actions, takže funguje i když je Mac vypnutý.

## Jak to funguje

- Workflow [`.github/workflows/watch.yml`](.github/workflows/watch.yml) běží
  **každou půlhodinu** (v :13 a :43 — mimo špičky, kdy GitHub cron nejvíc
  zahazuje běhy). Repo je veřejné, takže minuty Actions jsou zdarma bez limitu.
- [`watch.py`](watch.py) stáhne rozpis z veřejného JSON API cinemacity.cz
  (`/cz/data-api-service/v1/quickbook/10101/…`) — bez klíče, bez přihlášení.
- Seznam už viděných představení drží v [`state/seen.json`](state/seen.json),
  který si workflow po každém běhu commitne zpátky. Hlásí se tedy jen přírůstky.
- Nová představení → issue s časem, sálem, příznaky (70mm / titulky / vyprodáno)
  a přímým odkazem na nákup vstupenky. Hlásí se i termíny, které z rozpisu
  **zmizely** (zrušené projekce).
- U každého budoucího termínu se navíc kontroluje **obsazenost konkrétních
  sedadel** a hlásí se, když se nově uvolní místo v řadě 3 a dál. Podrobnosti
  níže v [Hlídání volných míst](#hlídání-volných-míst).
- Issue se **hned po založení zavírá**. Slouží jen jako doručovací kanál pro
  e-mail, který GitHub pošle už při jeho vzniku — seznam otevřených issues tak
  zůstává prázdný a nic není potřeba uklízet ručně. Obsah zůstává čitelný mezi
  zavřenými.
- Časy se počítají v zóně kina (`Europe/Prague`), ne v UTC runneru. Bez toho
  by projekce, která právě doběhla, vypadala jako budoucí a při zmizení
  z rozpisu by se falešně nahlásila jako zrušená.

Jeden běh je ~45 dotazů na rozpis plus 2 na každý budoucí termín kvůli místům
(~180 celkem) a trvá ~60 sekund.

## Hlídání volných míst

U vyprodaných projekcí je jediná šance dostat se dál od plátna to, že někdo
vrátí vstupenku. Watchdog proto u každého budoucího termínu kouká i na to,
která **konkrétní sedadla** jsou volná, a ozve se, když volné místo přibude
v řadě `BACK_ROW_MIN` (výchozí 3) a dál.

Dvě věci, na kterých to stojí a které nejsou z API vidět na první pohled:

- **Příznak `soldOut` z rozpisového API se nedá použít.** Zůstává na `0`
  i u projekcí, kde je volných posledních pár sedadel v první řadě. Obsazenost
  se proto zjišťuje z ticketingu (`tickets.cinemacity.cz/api`), ne z rozpisu.
- **Endpoint `seats-statusV2` vrací místa VOLNÁ, ne obsazená.** Co v odpovědi
  není, je prodané. Klíč `1_37_8` znamená sekce 1, index sedadla 37, index
  řady 8; popisky („řada 8, sedadlo 3") se dopárují z plánu sálu
  (`seatplanV2`), který se tahá jednou za běh a cachuje.

Ticketing API odmítá dotazy bez hlavičky `uuid` chybou 403. Frontend do ní dává
hodnotu ze stejnojmenné cookie, ale serveru stačí **jakékoli platné UUID** —
nemusí odpovídat žádné existující session, takže si ho watchdog vyrobí sám.
Celé to jede na čistém `urllib`, žádný headless prohlížeč není potřeba, i když
se seat mapa v prohlížeči kreslí až JavaScriptem.

Vozíčkářská místa (v plánu příznak `hc`) se **nepočítají** — jsou to vyhrazené
pozice, ne sedadlo, které by si šlo jen tak koupit. V sále `IMAX VOLVO` jsou
zrovna všechna v poslední řadě, takže bez téhle výjimky by watchdog hlásil
„volno vzadu" prakticky pořád.

Aby hlášení nechodilo každou půlhodinu dokola, ukládá se do stavu jen **seznam
řad** s volným místem (ne počty sedadel, ty se mění moc často) a hlásí se jen
řady, které oproti minule přibyly. Když se obsazenost nepodaří zjistit (výpadek
ticketingu, ukončený předprodej), převezme se poslední známá hodnota — jinak by
výpadek vypadal jako „místa zmizela" a jeho konec jako falešné uvolnění.

## Co přesně se hlídá

Představení, kde **název filmu** obsahuje `odyss` **a** **název sálu** obsahuje
`imax`. Aktuálně tomu odpovídá jediné kino v ČR — **Praha Flora**, sál
`IMAX VOLVO`, kde Odyssea běží v 70mm s titulky.

Aby se netahal celý rozpis všech třinácti kin, hledá se dvoufázově: nejdřív se
zjistí, která kina vůbec mají IMAX sál (jedna sonda na nejbližší hrací den plus
nápověda z API přes atribut `70-mm`), a do hloubky se projdou jen ta. Kdyby
IMAX přibyl v jiném kině, chytí se to samo.

Chování jde změnit proměnnými prostředí ve workflow:

| Proměnná | Výchozí | Význam |
| --- | --- | --- |
| `FILM_PATTERN` | `odyss` | podřetězec názvu filmu (case-insensitive) |
| `AUDITORIUM_PATTERN` | `imax` | podřetězec názvu sálu |
| `HORIZON_DAYS` | `180` | jak daleko dopředu se ptát |
| `HINT_ATTR` | `70-mm` | atribut pro levné dohledání kandidátských kin |
| `REQUEST_DELAY` | `0.25` | pauza mezi dotazy na API (s) |
| `BACK_ROW_MIN` | `3` | od které řady se místo bere jako „vzadu" (1 = u plátna) |
| `CHECK_SEATS` | `1` | `0` vypne hlídání míst, zůstanou jen nové termíny |

Hlídat cokoli jiného (třeba `FILM_PATTERN=dune`, `AUDITORIUM_PATTERN=4dx`) tedy
znamená přepsat dvě proměnné a smazat `state/seen.json`.

## Chci to hlídat taky (fork)

Watchdog nepotřebuje žádné tokeny ani secrets — API Cinema City je veřejné
a na zakládání issues stačí vestavěný `GITHUB_TOKEN`. Rozjedeš ho takhle:

1. **Forkni** si tohle repo.
2. **Settings → General → Features → zaškrtni `Issues`.** Forky mají issues
   vypnuté a bez nich by watchdog neměl kudy hlásit.
3. **Actions → „I understand my workflows, go ahead and enable them".**
   GitHub v forcích naplánované workflows nespouští, dokud je nepovolíš.
4. Hotovo. Issues se zakládají a přiřazují tobě, protože workflow používá
   `${{ github.repository_owner }}` — nic přepisovat nemusíš.

Stav v `state/seen.json` se forkne s sebou, takže tě to nezasype aktuálním
rozpisem a ozve se až s prvním novým termínem. Chceš-li hned vidět, co se
hraje teď, spusť workflow ručně s `force_report`.

Hlídat jiný film než Odysseu: přepiš `FILM_PATTERN` (a případně
`AUDITORIUM_PATTERN`) ve workflow a smaž obsah `state/seen.json`.

## Ruční spuštění

**Actions → Cinema City watchdog → Run workflow**. Zaškrtnutí *force_report*
nahlásí všechny aktuální termíny, i ty už známé — hodí se na ověření, že to žije,
nebo jako „ukaž mi, co teď hrajou“.

```bash
gh workflow run watch.yml --repo TarkDetrius/cinemacity-watchdog -f force_report=true
```

## Lokální spuštění

Čisté Python 3, žádné závislosti:

```bash
python3 watch.py --state state/seen.json
```

Užitečné přepínače: `--seed` (jen zapíše stav, nic nehlásí — dobré po změně
filtru), `--force-report` (vypíše vše bez ohledu na stav).

## Údržba

- **Kvóta Actions:** repo je záměrně veřejné — u veřejných rep jsou minuty
  Actions zdarma bez limitu. Kdyby se překlopilo na privátní, běhy by se začaly
  počítat do free limitu 2 000 minut měsíčně a půlhodinová kadence by ho
  přečerpala; pak je potřeba zároveň zpomalit cron (např. `23 */2 * * *`).
- **60denní pauza:** GitHub automaticky vypne cron, pokud v repu 60 dní nic
  nepřibude. Tady to nehrozí — workflow si sám commituje stav.
- **Až Odyssea dohraje,** watchdog jen přestane cokoli hlásit. Buď ho vypni
  (Actions → *Disable workflow*), nebo přepiš `FILM_PATTERN` na další film.
- Kdyby Cinema City API změnilo, workflow spadne s chybou a GitHub o tom
  pošle e-mail.
