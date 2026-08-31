# mangda_ritning – mängdning av CAD-exporterade VVS/VA-ritningar (PDF)

Ett fristående CLI-verktyg i Python som tar en CAD-exporterad PDF-ritning
(AutoCAD, typiskt A1/A0) och automatiskt:

1. hittar alla textbeteckningar/koder (OCR),
2. hittar alla rörledningar (vektorlinjer),
3. kopplar ihop kod med rörsträcka via ledartrådar,
4. **mängdar** – total rörlängd per typ/dimension, antal per komponentkod,
5. levererar en markerad PDF + mängdförteckning (XLSX/CSV) som kalkylunderlag.

## Grundprincip – två tekniker för två olika saker

AutoCAD exporterar text som **vektoriserade konturer** (särskilt SHX-typsnitt),
inte maskinläsbar text. Verktyget kontrollerar alltid först hur många riktiga
textord PDF:en innehåller (`page.get_text("words")`) kontra antal vektor-paths
(`page.get_drawings()`) – är ordantalet mycket lågt läses all synlig text med
OCR på en rastrerad bild. **Rörledningarna** däremot är riktiga vektorlinjer
med konsekvent bredd/färg och plockas direkt ur PDF:ens ritkommandon – de
OCR-läses aldrig.

| | Textkoder | Rörledningar |
|---|---|---|
| Datakälla | Rastrerad bild av sidan | PDF:ens vektor-ritkommandon |
| Metod | OCR (rutvis, flera PSM-lägen) | Geometri-parsning (bredd/färg/kedjning) |
| Bibliotek | `pytesseract` + PyMuPDF-rendering | PyMuPDF `page.get_drawings()` |

## Installation

```bash
pip install -r requirements.txt
# Tesseract med svenska språkpaketet krävs för OCR:
sudo apt-get install tesseract-ocr tesseract-ocr-swe
```

## Användning

```bash
# Standardkörning
python mangda_ritning.py ritning.pdf --output-dir out/

# Kalibreringsvy: visa linjebredd-histogrammet och välj rörkluster manuellt
python mangda_ritning.py ritning.pdf --calibrate
python mangda_ritning.py ritning.pdf --pipe-width 2.04 --pipe-color 0,0,0

# Explicit skala och vertikalhöjder, validering mot facit
python mangda_ritning.py ritning.pdf --scale 1:50 \
    --vertikalhojd "Rör tappvatten=2.8,Spill- dagvatten=2.8" \
    --facit facit.csv

# Exkludera legend/titelblock manuellt (PDF-punkter, upprepningsbar)
python mangda_ritning.py ritning.pdf --exclude-zone 2100,0,2384,1684
```

Se `python mangda_ritning.py --help` för alla flaggor (DPI, rutstorlek,
PSM-lägen, kod-regex, kedjetolerans, lager m.m.). Allt filspecifikt kan även
läggas i en JSON-konfigfil: `--config mall.json` (nycklarna = fälten i
`mangdning/config.py:Config`).

## Webbapp

Samma pipeline finns som webbapp: ladda upp PDF:en i webbläsaren, följ
bearbetningen och ladda ner alla resultatfiler. Formuläret har fält för
skala, sida, rörlinjebredd, vertikalhöjder, DPI, OCR-läge och facit-CSV.

```bash
# Lokalt (kräver samma beroenden som CLI:t, inkl. Tesseract)
uvicorn webapp.app:app
# öppna http://127.0.0.1:8000
```

Bearbetningen körs i en bakgrundstråd per jobb (ett åt gången som standard,
`WORKERS`-miljövariabeln höjer). Jobbfiler lagras i `jobs/` (`JOBS_DIR`)
och rensas efter 24 h (`JOB_TTL_SECONDS`). Maxstorlek på uppladdningar styrs
med `MAX_UPLOAD_MB` (standard 100).

### Publicera på nätet

OCR-körningarna tar flera minuter och kräver Tesseract som systemprogram –
välj därför en containerplattform, inte serverless (Vercel/Netlify har för
korta tidsgränser och saknar Tesseract). Repot innehåller en `Dockerfile`
som fungerar direkt på:

- **Railway** (enklast): railway.com → New Project → Deploy from GitHub repo
  → välj detta repo och branchen. Railway hittar Dockerfilen själv och
  publicerar en URL (Settings → Networking → Generate Domain).
- **Render**: render.com → New → Web Service → koppla repot, Runtime: Docker.
- **Fly.io**: `fly launch` i repokatalogen.

API:t: `POST /api/jobs` (multipart: `file`, valfritt `facit`, `scale`,
`page`, `pipe_width`, `vertikalhojd`, `dpi`, `ocr=auto|force|off`),
`GET /api/jobs/{id}` (status/progress/summering),
`GET /api/jobs/{id}/files/{namn}` (nedladdning), `GET /health`.

Observera att webbappen inte har någon inloggning – publicera den inte
öppet med känsliga ritningar utan att lägga plattformens skydd framför
(t.ex. Railways private networking, en access-proxy eller basic auth).

## Utdata

| Fil | Innehåll |
|---|---|
| `<namn>_markerad.pdf` | Markerad PDF: rör i stabil färg per kod (blå = okopplad), kodrutor (röd = ej kopplad till rör), ledartrådar – som separata PDF-lager (tänd/släck), styrs med `--layers codes,pipes,links` |
| `<namn>_koder.csv` | Kodtabell: kod, x/y-position, OCR-confidence, kopplad rörsträcka-id |
| `<namn>_mangder.xlsx` | Mängdförteckning i facit-struktur (flikar: radnivå, summering per kod, skala, varningar) |
| `<namn>_mangder.csv` | Samma radnivå som CSV |
| `<namn>_rapport.txt` | Körningsrapport: kopplingsgrad, skalfaktor och källa, kända begränsningar, kalibreringsunderlag |
| `<namn>_validering.txt` | Avvikelserapport mot facit (med `--facit`) |

Mängdförteckningens huvudflik är **en rad per sammanhängande rörsträcka**
(inte summerat per kod) med facit-filens kolumner (`Version, Document,
Subject, ..., Längd, unit, Lager, Antal_VS, Vertikal_höjd_VS,
Total_vertikalhöjd_VS, ...`) plus spårbarhetskolumnerna `Antal` och `Källa`
(rörsträcka-id/kodposition). Summeringen per kod ligger i en separat flik.

## Arkitektur

```
mangda_ritning.py        CLI-startpunkt
mangdning/
  config.py              all filspecifik konfiguration (regex, bredder, zoner …)
  models.py              datamodeller (OcrHit, CodeHit, PipeChain, QuantityRow …)
  ocr_codes.py           Del A: rendering ≥400 DPI, rutvis OCR (PSM 11+6),
                         dedup (text + position), Nx-notation, radparning
                         kod+dimension, exkluderingszoner
  pipes.py               Del B: linjebredd-histogram, dynamiskt klusterval,
                         ram/rutnätsfiltrering, union-find-kedjning,
                         vertikalsymboler (små cirklar vid rörändar)
  linking.py             Del C: ledartrådar (tunna diagonaler) kod→rör,
                         närhetsfallback, "ej kopplad"-flaggning
  scale.py               Del D1: skala från titelblock (OCR) + grafisk
                         skalstock, varning vid konflikt
  legend.py              Del D4/D7: systemkategorier och antalsuppgifter ur
                         legendens "FÖRKLARINGAR"/"SYSTEM …"-sektion
  quantify.py            Del D: radnivå per rörsträcka, Nx-multiplikation,
                         punktkomponenter, vertikalantal × konfigurerad höjd
  colors.py              stabil hex-färg per unik kod
  annotate.py            markerad PDF med OCG-lager
  report.py              XLSX/CSV/rapport-skrivare
  validate.py            Del D9: avvikelserapport mot facit-CSV
  pipeline.py            hela Del A-D som återanvändbar funktion
  cli.py                 argumentparsning + interaktiv skalverifiering
webapp/
  app.py                 FastAPI-app: jobbkö, status-API, filnedladdning
  static/index.html      uppladdningssida med progress och resultat
Dockerfile               container för Railway/Render/Fly.io
tests/                   77 tester inkl. integrationstest och webapp-API
```

## Kända begränsningar

- **OCR är inte 100 % träffsäker** på tät, liten CAD-text – enstaka koder kan
  saknas eller feltolkas (S/5, B/8, O/0). Mängderna är en **uppskattning**,
  inte en exakt mängdförteckning. Detta flaggas alltid i rapporten.
- **Linjebredd/färg-tröskeln är filspecifik** och kan behöva kalibreras om för
  andra ritningsmallar (`--calibrate`, `--pipe-width`).
- **Skalfaktorn är central** – verktyget visar alltid uppmätt skala plus en
  referenssträcka för bekräftelse innan mängderna presenteras (hoppas över med
  `--yes`, men skalfliken i XLSX:en ska alltid kontrolleras).
- **Vertikalhöjder är antaganden** (konfigurerade per system, t.ex. 2,80 m) –
  en planvy visar aldrig vertikal höjd geometriskt. Redovisas som
  `Antal_VS × Vertikal_höjd_VS = Total_vertikalhöjd_VS` och flaggas.
- Har man **originalfilen (DWG)** kan text och skala läsas exakt utan
  OCR/gissning – möjlig framtida importväg (inte byggd).

## Tester

```bash
python -m pytest tests/
```

Bl.a. verifieras att dedupliceringen aldrig kollapsar samma kodtext på
geometriskt skilda platser till en träff, att Nx-koder räknas som N parallella
rör, att kedjningen respekterar ändpunktstoleransen, och hela pipelinen körs
mot en syntetiskt genererad ritnings-PDF.
