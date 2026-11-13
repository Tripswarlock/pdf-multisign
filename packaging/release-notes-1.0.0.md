PDF Мультиподпись 1.0.0 — подпись PDF несколькими подписями CryptoPro за один проход.

## Что в релизе

- `PDF-Multisign.exe` (71 МБ) — portable-сборка для Windows 10/11 x64. Python устанавливать не нужно.
- `pdf-multisign_1.0.0_amd64.deb` (106 МБ) — пакет для Debian 12 / Astra Linux 1.8 / Ubuntu 22.04+.

## Возможности

- выбор PDF перетаскиванием или кнопкой, предпросмотр страниц;
- отметка нескольких сертификатов — подписи добавляются в один файл за один проход;
- видимый блок подписи (ФИО, организация, срок действия, дата) или невидимая подпись;
- каждая подпись — отдельная инкрементальная ревизия PDF, документ можно доподписывать;
- проверка подписи: CryptoPro CSP + независимый ГОСТ-хеш (Streebog-256);
- интерфейс PySide6 в стиле Material Design, тёмно-синий акцент;
- «О программе» с версией, компонентами и состоянием CryptoPro.

## Требования

- CryptoPro CSP 5.0 R2+ (CAdES Browser Plug-in на Windows) — устанавливается отдельно, в поставку не входит;
- сертификат с закрытым ключом в `CurrentUser\My` (Windows) или `uMy` (Linux), ключ — в контейнере CryptoPro.

## Установка

Windows: распакуйте и запустите `PDF-Multisign.exe`.

Linux:
```bash
sudo apt install ./pdf-multisign_1.0.0_amd64.deb
pdf-multisign
```
Пакет ставит приложение в `/opt/pdf-multisign` (зависимости Python внутри), ярлык в меню и `/usr/bin/pdf-multisign`.

## Проверено

- Windows 11 x64 + CryptoPro CSP 5.0, GOST R 34.10-2012: две подписи в одном PDF, обе проверены (VerifyCades + Streebog-256), PDFium видит 2 подписи;
- Astra Linux 1.8.6 в VirtualBox + CryptoPro CSP 5.0.13000: `.deb` установлен, интерфейс запускается, `cryptcp`/`certmgr` найдены, CMS с Windows проверяется на Linux (изменённый документ отклоняется).

## Контрольные суммы

```
SHA256  PDF-Multisign.exe                 57a8b78da2154f8db796fa8bb2a788f7fb36b89c4eae9feb5a765bde6cac7319
SHA256  pdf-multisign_1.0.0_amd64.deb     283a74e07bf8dcad753ca253dc4e6fef2d260c0bfb00f444dd4b638bca82f5fb
```

## Ограничения

- подписывать можно только сертификатами с ключом в CryptoPro CSP (ECC/RSA из Microsoft KSP не поддерживаются);
- цепочка доверия и отзыв (CRL/OCSP) не оцениваются, метка времени не ставится — офлайн-профиль;
- юридическая значимость зависит от CryptoPro CSP, сертификата и удостоверяющего центра.

Лицензия кода — MIT. Полное описание: [README](https://github.com/Tripswarlock/pdf-multisign#readme).
