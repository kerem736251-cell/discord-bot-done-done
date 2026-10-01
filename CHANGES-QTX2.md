> Update: Two-way synchronization is now implemented in the accompanying updated Pro/Elite managers. Earlier descriptions below of manual-only manager import apply to the original manager executables. Configure the HTTPS bot URL with the new **Discord sync** button. Both databases are preserved and merged; sync runs every 30 seconds while open. Conflict resolution uses server versions and saves a local conflict backup. Offline customer utilities still require signed revocation updates. Public verifier PEM files now live beside bot.py.

# Prüfung und Änderungen am Quantix-Key-System

Geprüft am 1. Oktober 2026. GitHub-Basis: `https://github.com/kerem736251-cell/discord-bot-done-done`, Commit `cf5cb71`.

## Gefundenes Lizenzsystem

Die aktuelle Linie der Utilities und die getrennten Desktop-Manager verwenden die gemeinsame C#-Bibliothek `QuantixLicensing`. Untersuchte Quellen unter:

`C:\Users\Quantix\Documents\Codex\2026-09-16\yo-x20\outputs\Quantix-Manager-Source`

- `elite/QuantixLicensing/QuantixLicensing/LicenseToken.cs`: Erzeugung, Signatur, Validierung.
- `HardwareFingerprint.cs` im selben Ordner: HWID-Ermittlung und Normalisierung.
- `ActivationStore.cs`, `OfflineRevocations.cs`: Aktivierungsspeicher und signierte Sperrdateien.
- `ManagerShared/Repository.cs`: Manager-Datenbank und Ausgabe/Import von Lizenzen.
- `pro/src/QuantixTweakingUtility.Services/LicenseService.cs` und entsprechender Elite-Service: Einbindung der Offline-Prüfung.

QTX2 ist **kein** zufälliger gruppierter Hex-Code. Das Format lautet `QTX2.<Base64url-JSON>.<Base64url-Signatur>`. JSON-Felder sind `version=2`, `product`, `licenseId`, `customer`, `hwidHash`, `issuedAtUtc` und optional `expiresAtUtc`. Eine fehlende Ablaufzeit bedeutet permanent. `licenseId` ist eine 32-stellige UUID ohne Bindestriche. Produkte sind `quantix-pro` und `quantix-elite`.

Signiert wird der exakte UTF-8-Text `QTX2.` plus kodierter JSON-Body mit ECDSA P-256/SHA-256. Die Signatur enthält die beiden 32-Byte-Werte r und s in IEEE-P1363-Darstellung, nicht DER. Beide Editionen haben getrennte Schlüssel. Der öffentliche Schlüssel ist in der jeweiligen Utility eingebettet.

Die **HWID** beginnt dagegen mit `QX2-` und besteht aus acht Gruppen mit je acht Hex-Zeichen. Intern wird der SHA-256-Hash von `QUANTIX-HWID-V2|<Windows-MachineGuid>|<SMBIOS-UUID>` verwendet. Die Utility normalisiert Groß-/Kleinschreibung, Bindestriche und Leerzeichen. Der Lizenz-Key selbst ist wegen Base64url groß-/kleinschreibungssensitiv. Eine Windows-Neuinstallation kann die HWID ändern.

Die Prüfung kontrolliert Signatur, Edition, Payload-Struktur, vollständige HWID und Zeit. Mehr als fünf Minuten in der Zukunft ausgestellte Lizenzen werden abgelehnt. `expiresAtUtc <= jetzt` ist abgelaufen. Die Utility prüft außerdem eine vorhandene lokale signierte Sperrdatei.

Lokale Speicherorte:

- Aktivierung: `%LOCALAPPDATA%\Quantix\LicensesV2\quantix-pro.key` beziehungsweise `quantix-elite.key`.
- Offline-Sperrliste: gleicher Ordner, `quantix-pro.qrv` beziehungsweise `quantix-elite.qrv`.
- Manager-Registry: `%LOCALAPPDATA%\Quantix\LicenseManagersV2\pro\licenses.json` beziehungsweise `elite\licenses.json`, mit `.bak`-Sicherung.

Die geprüften Utilities rufen keine Lizenz-API auf. Auch Elite `ValidateTokenOnlineAsync` leitet lokal an den Validator weiter. Ältere Recovery/Owner-Rebuild-Ordner enthalten andere Generationen; diese Änderung zielt auf die getestete QTX2-Linie Pro 2.8.0/Elite 3.9.3 und die dazu passenden Schlüssel aus den jeweiligen Pro-/Elite-License-Managern.

## Geänderte Dateien und Verhalten

| Datei | Änderung |
|---|---|
| `bot.py` | Echte QTX2-Ausstellung und Registrierung; private DM/Datei; Staff-Prüfung beibehalten; Info/Liste mit Lizenz-ID; Verlängern/HWID-Wechsel stellen Ersatzkeys aus; Wiederholung der Zustellung; Sperrdatei-Import/-Export; API erhält Groß-/Kleinschreibung und normalisiert HWID; DB-Verzeichnis wird angelegt; Fehlermeldungen geben keine rohen Exception-Inhalte aus. |
| `quantix_licensing.py` | Exakter QTX2/QRV2-Signaturaufbau, HWID-Prüfung, Editionsprüfung, Originalschlüssel aus geheimen Variablen oder Dateipfaden, Abgleich mit den öffentlichen Schlüsseln, Prüfung importierter Sperrdateien. |
| `license_registry.py` | Migration, transaktionale Speicherung, Lookup über Key/ID, Ersetzung mit Historie, kumulative Sperren und monotone Revisionen. |
| `license_public_keys/` | Nur die beiden öffentlichen Schlüssel aus den jeweiligen Pro-/Elite-License-Managern. Keine privaten Schlüssel. |
| `requirements.txt` | `cryptography==50.0.2` ergänzt; bestehende Abhängigkeiten unverändert. |
| `.gitignore`, `.dockerignore`, `.env.example` | Konfigurationsvorlage und Ausschluss privater Schlüssel, Datenbanken, Lizenzdateien, Umgebungsdateien und Python-Cache. |
| `tests/test_licensing.py` | Automatisierte Tests ohne echte Discord-Verbindung und ohne Produktionsschlüssel. |
| `README.md`, `LICENSE_KEY_SETUP.md`, dieser Bericht | Korrekte Offline-Architektur, Bedienung, Migration und Deployment. |

Schema-Erweiterung ohne Löschen alter Daten: `licenses.license_id`, `customer_name`, `replaced_by`, eindeutiger Index auf `license_id`; neue Tabellen `license_revisions` und `imported_revocations`. Alte zufällige Keys bleiben erhalten, werden aber nicht automatisch in echte Offline-Keys umgewandelt.

Der bisherige Zufallsgenerator `make_license_key` wurde entfernt. 38 andere vorhandene Funktionen/Klassen sind strukturell unverändert, darunter die bestehenden Ticket-, Rules-/Verified-, Review-, Welcome-, Announcement- und Giveaway-Funktionen. Das konkrete GitHub-Repository ist die Basis; zusätzliche Funktionen aus anderen älteren lokalen Bot-Paketen wurden nicht hineingemischt.

## Tests und Grenzen

13 Python-Tests bestanden mit Python 3.12.14 und exakten Paketversionen. Sie prüfen unter anderem beide Editionen und alle fünf Dauern, Migration des echten alten Tabellenschemas, Erhalt anderer Tabellen, Ablaufgrenze, ungültige HWID/Signierschlüssel, Transaktions-Rollback, Ersetzungsverlauf, Staff-Zugriff, DM nach Commit, DM-Fehler, API-Prüfung und Zusammenführung von Sperrdateien.

Zusätzlich 20 erfolgreiche Prüfungen mit der vorhandenen C#-Bibliothek und 14 mit den echten EXEs aus `Quantix-Pro-2.8.0` und `Quantix-Elite-3.9.3`. Beide EXEs akzeptierten befristete/permanente Python-signierte Keys und lehnten falsche HWID, abgelaufene/zukünftige Keys, Signaturmanipulation und falsche Edition ab. Verifikation erfolgte über die vorhandenen Prüfmodi, ohne Aktivierung oder Tweaks.

Keine bestehende Manager-Datenbank und keine gespeicherte Aktivierung wurden geändert. Bot-Registrierung liegt in SQLite auf Railway; die Desktop-Manager-Liste synchronisiert sich nicht automatisch. Dort funktioniert der vorhandene manuelle Key-Import.

Offline-Sperren wirken erst nach Import einer kumulativen signierten `.qrv` auf dem Kunden-PC. Alte Keys bleiben ansonsten bis zum signierten Ablauf gültig. Details zum Zusammenführen vorhandener Manager-Sperren und zur einzigen Veröffentlichungsstelle stehen in `LICENSE_KEY_SETUP.md`.

GitHub-Push, Railway-Deployment und echter Discord-DM-Versand sind noch nicht durchgeführt. Die geprüfte Änderung ist als lokales Projekt und ZIP bereitgestellt. Bestehende Live-Variablen wurden nicht gelesen oder geändert.

## Abgleich der ausdrücklich benannten Manager

Direkt geprüft: `Quantix-Elite-License-Manager/QuantixEliteLicenseManager.exe` und `Quantix-Pro-License-Manager/QuantixProLicenseManager.exe` im ursprünglichen outputs-Ordner. Beide integrierten Selbsttests bestanden jeweils 13 Prüfungen an isolierten Testpfaden. Private und öffentliche Schlüssel wurden direkt aus deren jeweiligen `keys`-Ordnern geprüft, ohne sie auszugeben. Beide Schlüsselpaare entsprechen exakt den zuvor verwendeten Generator-v2-Schlüsseln und den öffentlichen Bot-Schlüsseln. Anleitung und weitere Tests verwenden jetzt ausdrücklich die Manager-Pfade. Die automatisierte Synchronisierung der lokalen Manager-Listen ist weiterhin nicht Bestandteil der vorhandenen Offline-Architektur.
