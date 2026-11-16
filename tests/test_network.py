"""Offline protocol test for LAN signing sessions: no token required.

Two «participants» sign through a real HTTP server on localhost with a stub
CMS built by asn1crypto (messageDigest = Streebog-256 of the document).

Run with:  python tests/test_network.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))

from asn1crypto import algos as asn1algos  # noqa: E402
from asn1crypto import cms as asn1cms  # noqa: E402
from asn1crypto import core as asncore  # noqa: E402
from asn1crypto import x509 as asn1x509  # noqa: E402

from pdfsign import crypto, netsession, pdf as pdfmod  # noqa: E402


def make_test_cms(content: bytes) -> bytes:
    """Parseable SignedData whose messageDigest equals Streebog-256(content)."""
    digest = bytes(crypto.streebog256(content))
    assert digest, "gostcrypto недоступен"
    signed_attrs = asn1cms.CMSAttributes([
        asn1cms.CMSAttribute({
            "type": asn1cms.CMSAttributeType("message_digest"),
            "values": [digest],
        }),
    ])
    signer_info = asn1cms.SignerInfo({
        "version": asn1cms.CMSVersion(1),
        "sid": asn1cms.SignerIdentifier({
            "issuer_and_serial_number": asn1cms.IssuerAndSerialNumber({
                "issuer": asn1x509.Name.build({"common_name": "stub"}),
                "serial_number": asncore.Integer(1),
            }),
        }),
        "digest_algorithm": asn1algos.DigestAlgorithm({"algorithm": "sha256"}),
        "signed_attrs": signed_attrs,
        "signature_algorithm": asn1algos.SignedDigestAlgorithm(
            {"algorithm": "rsassa_pkcs1v15"}
        ),
        "signature": asncore.OctetString(b"\x00" * 32),
    })
    signed_data = asn1cms.SignedData({
        "version": asn1cms.CMSVersion(3),
        "digest_algorithms": [asn1algos.DigestAlgorithm({"algorithm": "sha256"})],
        "encap_content_info": asn1cms.EncapsulatedContentInfo({
            "content_type": asn1cms.ContentType("data"),
            "content": b"",
        }),
        "signer_infos": [signer_info],
    })
    return asn1cms.ContentInfo({
        "content_type": asn1cms.ContentType("signed_data"),
        "content": signed_data,
    }).dump()


def find_fixture() -> pathlib.Path:
    candidates = [
        ROOT / "fixtures" / "unsigned.pdf",
        pathlib.Path(r"D:\Deepseek-harness\git\stamp-ep-sa-main\stamp-ep-sa-main\fixtures\unsigned.pdf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Не найден тестовый PDF (unsigned.pdf)")


CERT = {
    "thumbprint": "A1B2C3D4E5F6",
    "subject": "CN=Тестовый Подписант",
    "issuer": "CN=Тестовый УЦ",
    "serial": "01",
    "surname": "Подписант",
    "given_name": "Тестовый",
    "organization": "ООО «Тест»",
    "position": "Директор",
    "not_before": "2026-01-01T00:00:00",
    "not_after": "2028-01-01T00:00:00",
}


def main() -> int:
    source = find_fixture().read_bytes()
    code = "424242"
    state = netsession.SessionState(source, "test.pdf", code, visible=False)
    server = netsession.SessionServer(state, port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    failures: list[str] = []

    try:
        # wrong code rejected
        try:
            netsession.session_info(base, "000000")
            failures.append("неверный код должен быть отклонён")
        except netsession.SessionError as exc:
            if exc.status != 403:
                failures.append(f"неверный код: ожидался 403, получен {exc.status}")

        # join + fetch document
        pid_one, info = netsession.join_session(base, code, "Первый")
        if info.get("signatures") != 0:
            failures.append("в начале сессии подписей быть не должно")
        document = netsession.fetch_document(base, code)
        if document != source:
            failures.append("документ участника отличается от исходного")

        # first participant: prepare -> sign the exact bytes -> commit
        signed_bytes = netsession.prepare_signature(base, code, pid_one, CERT)
        if not signed_bytes.startswith(b"%PDF-"):
            failures.append("подготовленные байты должны начинаться с заголовка PDF")
        cms_one = make_test_cms(signed_bytes)
        field_one, _ = netsession.commit_signature(base, code, pid_one, cms_one)
        if field_one != "Signature1":
            failures.append(f"ожидалось Signature1, получено {field_one}")
        info = netsession.session_info(base, code)
        if info.get("signatures") != 1:
            failures.append("после первой подписи должна быть 1")
        statuses = {p["pid"]: p["status"] for p in info.get("participants") or []}
        if statuses.get(pid_one) != netsession.STATUS_SIGNED:
            failures.append("статус первого участника должен быть signed")

        # second participant is queued while the first prepares a signature
        pid_two, _ = netsession.join_session(base, code, "Второй")
        reserved = netsession.prepare_signature(base, code, pid_two, CERT)
        try:
            netsession.prepare_signature(base, code, pid_one, CERT)
            failures.append("второй prepare должен быть отклонён с BUSY")
        except netsession.SessionError as exc:
            if exc.code != "BUSY":
                failures.append(f"ожидался код BUSY, получен {exc.code}")
        # wrong participant cannot commit another's reservation
        wrong_cms = make_test_cms(reserved)
        try:
            netsession.commit_signature(base, code, pid_one, wrong_cms)
            failures.append("чужой commit должен быть отклонён")
        except netsession.SessionError as exc:
            if exc.code != "NO_PENDING":
                failures.append(f"ожидался код NO_PENDING, получен {exc.code}")
        # digest mismatch discards the reservation and marks the participant
        bad_cms = make_test_cms(b"not the prepared bytes")
        try:
            netsession.commit_signature(base, code, pid_two, bad_cms)
            failures.append("подпись с неверным хешем должна быть отклонена")
        except netsession.SessionError as exc:
            if exc.code != "DIGEST_MISMATCH":
                failures.append(f"ожидался код DIGEST_MISMATCH, получен {exc.code}")
        info = netsession.session_info(base, code)
        statuses = {p["pid"]: p["status"] for p in info.get("participants") or []}
        if statuses.get(pid_two) != netsession.STATUS_ERROR:
            failures.append("после неверной подписи статус должен быть error")
        if info.get("signatures") != 1:
            failures.append("неверная подпись не должна добавлять ревизию")

        # after cancel/error the reservation is free again: second signs properly
        netsession.cancel_signature(base, code, pid_two)
        fresh_bytes = netsession.prepare_signature(base, code, pid_two, CERT)
        cms_two = make_test_cms(fresh_bytes)
        field_two, _ = netsession.commit_signature(base, code, pid_two, cms_two)
        if field_two != "Signature2":
            failures.append(f"ожидалось Signature2, получено {field_two}")

        # final document structure
        final = state.pdf_bytes
        fields = pdfmod.list_signatures(final)
        if len(fields) != 2:
            failures.append(f"ожидалось 2 подписи в документе, получено {len(fields)}")
        if fields and not fields[-1].covers_whole_file:
            failures.append("последняя подпись должна покрывать весь файл")
        for field in fields:
            digest = crypto.cms_message_digest(field.contents)
            actual = crypto.streebog256(field.signed_bytes)
            if digest is None or actual is None or digest != actual:
                failures.append(f"{field.field_name}: хеш подписи не совпадает с подписанными байтами")
        info = state.info()
        if info.get("signatures") != 2:
            failures.append("info: должно быть 2 подписи")
        signed = [p for p in info.get("participants") or [] if p["status"] == "signed"]
        if len(signed) != 2:
            failures.append("должно быть 2 участника со статусом signed")
    finally:
        server.stop()

    if failures:
        print("FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("OK: сетевая сессия — очередь prepare/commit, статусы, отказ чужой и неверной подписи")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
