"""Offline parser test for localized CryptoPro ``certmgr`` output.

On a Russian Astra certmgr prints «Субъект», «Издатель», «SHA1 отпечаток»;
the parser must handle both that and the C-locale English output.  The dates
CryptoPro prints are dd/mm/yyyy regardless of locale.

Run with:  python tests/test_crypto_linux.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from pdfsign import crypto_linux  # noqa: E402

RUSSIAN = '''
1-------
Издатель            : ИНН ЮЛ=7708410783, C=RU, CN="ППК ""Роскадастр"""
Субъект             : ИНН=244800396938, G=Юрий Алексеевич, SN=Трепачев, CN=Трепачев Юрий Алексеевич
Серийный номер      : 0x0227E12D00AFB37A80429F830A2CC08466
SHA1 отпечаток      : b3717f612b97ffbe274854ba02f1a89ae7ec4a1a
Алгоритм откр. кл.  : ГОСТ Р 34.10-2012 256 бит (512 бит)
Выдан               : 10/12/2025 02:37:03 UTC
Истекает            : 10/03/2027 02:47:03 UTC
Ссылка на ключ      : Есть
Контейнер           : SCARD\\JACARTA_4E46000630543932\\cc00\\C4AA
Имя провайдера      : Crypto-Pro GOST R 34.10-2012 KC1 CSP
=============================================================================
'''

ENGLISH = '''
1-------
Issuer              : INN=7708410783, C=RU, CN="PPK Roskadastr"
Subject             : INN=244800396938, G=Yuriy Alekseevich, SN=Trepachev, CN=Trepachev Yuriy Alekseevich
Serial              : 0x0227E12D00AFB37A80429F830A2CC08466
SHA1 Thumbprint     : b3717f612b97ffbe274854ba02f1a89ae7ec4a1a
Not valid before    : 10/12/2025 02:37:03 UTC
Not valid after     : 10/03/2027 02:47:03 UTC
PrivateKey Link     : Yes
Container           : SCARD\\JACARTA_4E46000630543932\\cc00\\C4AA
Provider Name       : Crypto-Pro GOST R 34.10-2012 KC1 CSP
=============================================================================
'''


def check_parse(label: str, text: str, failures: list[str]) -> None:
    certificates = crypto_linux._parse_certmgr(text)
    if len(certificates) != 1:
        failures.append(f"{label}: ожидался 1 сертификат, получено {len(certificates)}")
        return
    cert = certificates[0]
    if cert.thumbprint != "B3717F612B97FFBE274854BA02F1A89AE7EC4A1A":
        failures.append(f"{label}: неверный отпечаток {cert.thumbprint!r}")
    if not cert.sign_capable:
        failures.append(f"{label}: сертификат должен быть пригоден для подписи")
    if not cert.is_valid_now:
        failures.append(f"{label}: сертификат должен быть действующим")
    if cert.not_before is None or cert.not_before.strftime("%d.%m.%Y %H:%M") != "10.12.2025 02:37":
        failures.append(f"{label}: неверная дата начала {cert.not_before}")
    if cert.not_after is None or cert.not_after.strftime("%d.%m.%Y %H:%M") != "10.03.2027 02:47":
        failures.append(f"{label}: неверная дата окончания {cert.not_after}")
    if not cert.container_name.startswith("SCARD\\JACARTA"):
        failures.append(f"{label}: не найден контейнер {cert.container_name!r}")


def main() -> int:
    failures: list[str] = []
    check_parse("русский вывод", RUSSIAN, failures)
    check_parse("английский вывод", ENGLISH, failures)
    if crypto_linux._parse_certmgr("") != []:
        failures.append("пустой вывод должен давать пустой список")
    if failures:
        print("FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("OK: certmgr разбирается в русской и английской локали, даты dd/mm/yyyy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
