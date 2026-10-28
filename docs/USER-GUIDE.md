# domeware user guide / käyttöohje

**EN** below the Finnish part. Commands are the same in both.

## Suomeksi

domeware kokoaa yhden testipäivän datan (tutka, droonilokit, video, sää) yhdelle UTC-aikajanalle, näyttää sen
oikeassa maastossa ja laskee luvut scorecardeihin. Raakatiedostoja ei koskaan muuteta.

**Kentällä, ennen lähtöä** (ei vaadi nettiä):
1. Kopioi kaikki tiedostot yhteen kansioon (tutkan XML ja config, droonilokit .bin / .tlog / .SRT + .MP4, säädata).
2. `uv run dw ingest <sessio> <kansio>`
3. `uv run dw check <sessio>`: korjaa jokainen FAIL ennen lähtöä.

**Ajon aikana:** täytä Run Scorecards -sivu. Now-napit kirjoittavat vaiheiden UTC-ajat. Ajon jälkeen paina Export JSON.

**Illalla tai seuraavana päivänä:**
1. `uv run dw run <sessio> <kansio>`: muunnos, maasto, sää, replay, vertailu ja raportti (1–3 min).
2. `uv run dw runs <sessio> --card <export>.json`: vaiheajat korteilta.
3. `uv run dw scorecard <sessio>`, sitten sivulla **Import domeware values**. Pass / Fail valitaan itse.
4. `uv run dw view <sessio>`: 3D-replay. `views\report.html` aukeaa myös puhelimella.

**Tiedostot sessiossa:** `raw/` (alkuperäiset, hashattu), `streams/` (Parquet), `env/` (maasto), `views/` (replay,
raportti, scorecard-arvot), `runs.csv` (ajot ja vaiheet, muokattavissa), `video_offsets.csv` (videoiden ajat, jos
SRT puuttuu).

**Muistiinpanot ja datan siirto:** `uv run dw note <sessio> "lintuparvi idässä" --at 09:14:30` lisää muistiinpanon.
`uv run dw seal <sessio>` ja sitten `uv run dw sync <sessio> E:\` kopioi session levylle ja tarkistaa kopion.

**Jos jokin menee pieleen:** `uv run dw doctor` kertoo, mitä koneelta puuttuu. Punainen virheteksti Claudelle.

## In English

domeware puts one test day (radar, drone logs, video, weather) on one UTC timeline, replays it in the real terrain
and measures the scorecard numbers. Raw files are never changed.

**In the field, before leaving** (offline): copy everything into one folder, `dw ingest <session> <folder>`, then
`dw check <session>` and fix every FAIL.

**During the run:** fill the Run Scorecards page; the Now buttons write UTC phase times. Export JSON afterwards.

**Afterwards:** `dw run <session> <folder>` (everything, 1-3 min), `dw runs <session> --card <export>.json`,
`dw scorecard <session>`, then **Import domeware values** on the page and set Pass / Fail yourselves.
`dw view <session>` opens the 3D replay; `views/report.html` opens on a phone.

**Notes and moving data:** `dw note <session> "text" --at 09:14:30`; `dw seal <session>`, then
`dw sync <session> <disk>` copies the session and checks the copy.

**Something wrong:** `dw doctor` lists what the laptop is missing.
