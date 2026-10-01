> Update: Two-way synchronization is now implemented in the accompanying updated Pro/Elite managers. Earlier descriptions below of manual-only manager import apply to the original manager executables. Configure the HTTPS bot URL with the new **Discord sync** button. Both databases are preserved and merged; sync runs every 30 seconds while open. Conflict resolution uses server versions and saves a local conflict backup. Offline customer utilities still require signed revocation updates. Public verifier PEM files now live beside bot.py.

# Quantix QTX2: Einrichtung und Betrieb

Stand: 1. Oktober 2026. Basis: `kerem736251-cell/discord-bot-done-done`, Commit `cf5cb71`.

## Ergebnis

`/key create` erzeugt echte, editionsgebundene QTX2-Lizenzen mit denselben Signierschlüsseln wie die vorhandenen Pro- und Elite-License-Manager. Die unveränderten Utilities Pro 2.8.0 und Elite 3.9.3 akzeptieren diese Keys. Der Bot registriert sie vor dem Versand in seiner SQLite-Datenbank und schickt sie dem Kunden als DM plus `.key`-Datei.

Die Utility arbeitet offline. Sie liest nicht die Bot-Datenbank: Die Signatur beweist die Ausstellung durch den Besitzer. Die Desktop-Manager-Liste wird nicht automatisch mit Railway synchronisiert. Ein Bot-Key lässt sich im passenden Desktop-Manager über **Import key** aufnehmen.

## Railway: konkrete Schritte

1. Vorhandene Railway-Datenbank sichern. Den aktualisierten Projektinhalt ins bestehende GitHub-Repository übernehmen: insbesondere `bot.py`, `quantix_licensing.py`, `license_registry.py`, `license_public_keys/`, `requirements.txt` und Konfigurationsdateien. Keine privaten PEM-Dateien hochladen. Die alte `bot.cpython-313.pyc` wird nicht benötigt.
2. Persistentes Railway-Volume mit dem Bot-Service verbinden, Mount Path `/data`. `DB_PATH=/data/quantix.db` setzen. Bei Änderung eines bisherigen DB-Pfads die vorhandene Datenbank vorher sichern und übernehmen; der neue Pfad übernimmt keine Daten automatisch. Nur eine Bot-Instanz mit dieser SQLite-Datei betreiben.
3. Zwei neue geheime Service-Variablen setzen, idealerweise sealed variables:

   | Variable | Inhalt |
   |---|---|
   | `QUANTIX_PRO_PRIVATE_KEY_PEM` | Vollständiger Inhalt der vorhandenen `pro-signing-private.pem` aus dem Pro-License-Manager |
   | `QUANTIX_ELITE_PRIVATE_KEY_PEM` | Vollständiger Inhalt der vorhandenen `elite-signing-private.pem` aus dem Elite-License-Manager |

   Die vorhandenen Dateien liegen auf diesem PC unter:

   - Pro: `C:\Users\Quantix\Documents\Codex\2026-09-16\yo-x20\outputs\Quantix-Pro-License-Manager\keys\pro-signing-private.pem`

   - Elite: `C:\Users\Quantix\Documents\Codex\2026-09-16\yo-x20\outputs\Quantix-Elite-License-Manager\keys\elite-signing-private.pem`

   PEM-Kopfzeile, Inhalt und Endzeile vollständig übernehmen. Echte Zeilenumbrüche oder literale `\n` werden unterstützt. Nicht die Schlüssel älterer Owner-Rebuild-Versionen verwenden und keine neuen generieren. Die Utility würde solche Lizenzen ablehnen. Der Bot prüft den privaten Schlüssel gegen den mitgelieferten öffentlichen Schlüssel.
4. Vorhandene Discord-, Guild-, Channel-, Rollen- und Review-Variablen beibehalten. `DISCORD_TOKEN` bleibt erforderlich. Railway darf `PORT` setzen; die bestehende API läuft weiterhin auf diesem Port.
5. Redeploy. Der Bot erweitert die vorhandene Datenbank ohne alte Datensätze zu löschen. Die bestehende Slash-Command-Synchronisierung registriert die aktualisierten Befehle.
6. `/key create` mit Kunde, Edition, vollständiger **QX2-HWID aus dessen Utility** und Dauer ausführen. Der Kunde kopiert den kompletten `QTX2.…`-Key aus der DM oder `.key`-Datei in die passende Utility.

`LICENSE_API_SECRET` ist für QTX2 nicht erforderlich und ersetzt keinen Signierschlüssel. Die vorhandene Utility ruft `/api/license/validate` nicht auf. Der bestehende Server-Endpunkt prüft weiterhin Key + HWID und verlangt kein `LICENSE_API_SECRET`. Die Variable wird auch für den optionalen ausgehenden `/verify`-API-Aufruf verwendet. Keinen privaten Signierschlüssel oder administrativen API-Schlüssel in Kunden-Utilities einbauen.

Railway-Dokumentation: [Volumes](https://docs.railway.com/volumes), [Variablen und sealed variables](https://docs.railway.com/variables).

## Befehle und Altdaten

- `/key create`: 1 Tag, 1 Woche, 30 Tage, 365 Tage oder permanent. HWID-Normalisierung entspricht der Utility. Speicherung erfolgt vor der DM.
- `/key info`: Key oder Lizenz-ID eingeben. Zeigt Registry-Status, Edition, Kunde, HWID und Ablauf privat.
- `/keys user`: Bis zu 20 Einträge mit für Verwaltungsbefehle nutzbaren Lizenz-IDs.
- `/key resend`: Erneuter Versand einer aktiven QTX2-Lizenz. Bei blockierter DM erhält nur der ausführende Mitarbeiter eine private Datei zum manuellen Weitergeben.
- `/key extend`: Neuer signierter Key mit neuer ID per DM. Verlängert ab dem späteren Zeitpunkt von bisherigem Ablauf und jetzt. Permanent bleibt permanent. Kunde muss den neuen Key aktivieren.
- `/key reset-hwid`: Neuer Key mit neuer ID und HWID, bei gleichem Ablaufdatum. Für abgelaufene oder gesperrte Keys nicht verfügbar.
- `/key revoke`: Sofortige Sperre im Bot/API. Die Offline-Utility erfährt davon erst durch ein importiertes Lizenz-Update.
- `/key import-update`: Signierte `.qrv` aus Desktop-Manager importieren. Sperr-IDs werden vereinigt; bestehende Sperren nicht aufgehoben.
- `/key export-update`: Kumulative, editionsbezogene `.qrv` exportieren und auf Kunden-PC über **LICENSE UPDATE** beziehungsweise **IMPORT LICENSE UPDATE** einlesen.

Alle Lizenzbefehle behalten die bisherige Staff-Regel: Administrator, Manage Server oder konfigurierte Support-Rolle. Lizenzinhalte erscheinen nicht in öffentlichen Ereignis-Logs. Private DM-Dateien enthalten den Kundenschlüssel.

Alte zufällige `QTX-PRO-…`/`QTX-ELITE-…`-Codes bleiben in Datenbank und API erhalten, werden dadurch aber nicht zu gültigen Offline-Lizenzen. Einen richtigen Key mit `/key create` ausstellen. Ein noch aktiver Alt-Eintrag kann alternativ per `/key reset-hwid` mit vollständiger HWID neu ausgestellt werden. Keine Altlizenz wird automatisch umgeschrieben.

## Offline-Sperren und Desktop-Manager

Bei Verlängerung/HWID-Wechsel bleibt der alte Eintrag als gesperrte Historie erhalten. Sein Key bleibt offline bis zum Ablauf oder Import der Sperrdatei gültig. Ein permanenter alter Key kann ohne Update weiterhin funktionieren.

Vor der ersten Verteilung einer Bot-Sperrdatei in jedem bereits verwendeten Desktop-Manager **Export offline update** ausführen und die passende Datei mit `/key import-update` im Bot zusammenführen. Danach den Bot als einzige Stelle für kumulative Sperrdateien verwenden. Weitere Manager-Sperren vor einem neuen Export erneut importieren. Unabhängig verteilte Listen können sonst Sperren der jeweils anderen Liste entfernen. Importierte Sperren werden nicht automatisch wiederhergestellt; bei Bedarf einen neuen Key ausstellen.

Die Utility prüft Signatur, Edition und steigende Revision einer Sperrdatei. Offline-Betrieb bietet keine verlässliche Fernsperre; lokale Administratoren können lokale Sperrdateien entfernen. Sofortige Online-Sperren erfordern separate Änderungen an beiden Utilities.

## Lokaler Test

Python 3.12 wie im Dockerfile verwenden:

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests erzeugen eigene Schlüssel und temporäre Datenbanken. Kein Bot-Start oder Discord-Versand. Für eigene echte lokale Tests können alternativ `QUANTIX_PRO_PRIVATE_KEY_FILE` und `QUANTIX_ELITE_PRIVATE_KEY_FILE` auf vorhandene private PEM-Dateien zeigen. `_PEM` hat Vorrang.

## Durchgeführte Verifikation

- 13 Python-Tests bestanden, Python 3.12.14 mit exakten Abhängigkeiten: beide Editionen/alle Dauern, Migration des alten Schemas, Bestandserhalt, HWID/Ablauf, Schlüsselabgleich, Ersetzung, Transaktionsfehler, Staff-Prüfung, DM-Fehler, kumulative Sperrdateien und lokale HTTP-API.
- 20 Prüfungen mit vorhandener `QuantixLicensing.dll` bestanden, inklusive Signatur/HWID/Zeit/Edition und Sperrdatei-Import/Abfrage an isolierten Testpfaden.
- 14 Prüfungen mit echten EXEs Pro 2.8.0 und Elite 3.9.3 bestanden: befristet/permanent akzeptiert; falsche HWID, abgelaufen, zukünftig, manipuliert und falsche Edition abgelehnt.
- Keine Aktivierung gespeichert, keine Windows-Tweaks ausgeführt, keine vorhandenen Manager-Datenbanken verändert.
- Kein Live-Discord-Versand, Railway-Deployment oder GitHub-Push durchgeführt. Aktualisiertes Projekt und ZIP liegen lokal vor.

Die vom Nutzer ausdrücklich benannten Manager-EXEs wurden zusätzlich jeweils mit ihrem integrierten Selbsttest geprüft: Pro 13/13 und Elite 13/13 erfolgreich. Die Tests nutzten isolierte Datenbanken. Schlüsselpaare beider Manager stimmen mit den öffentlichen Bot-Schlüsseln überein; sie sind auch identisch mit den zuvor geprüften Generator-v2-Schlüsseln.
