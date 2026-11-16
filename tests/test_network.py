"""Offline test for LAN signing sessions: real HTTP server on localhost.

Sequential mode: two participants sign through the prepare/commit flow with a
stub CMS built by asn1crypto (messageDigest = Streebog-256 of the exact bytes
handed out by the host).  Parallel mode: detached .sig collection.

Run with:  python tests/test_network.py
"""
from __future__ import annotations

import base64
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

from pdfsign import crypto, network, pdf as pdfmod  # noqa: E402


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


class StubCert:
    def __init__(self, name: str, thumb: str):
        self.display_name = name
        self.organization = "ООО «Тест»"
        self.position = "Директор"
        self.common_name = name
        self.validity_text = "01.01.2026 — 01.01.2028"
        self.serial = "01"
        self.thumbprint = thumb


def check_final_document(data: bytes, expected: int, failures: list[str]) -> None:
    fields = pdfmod.list_signatures(data)
    if len(fields) != expected:
        failures.append(f"ожидалось {expected} подписей в документе, получено {len(fields)}")
    if fields and not fields[-1].covers_whole_file:
        failures.append("последняя подпись должна покрывать весь файл")
    for field in fields:
        digest = crypto.cms_message_digest(field.contents)
        actual = crypto.streebog256(field.signed_bytes)
        if digest is None or actual is None or bytes(digest) != bytes(actual):
            failures.append(f"{field.field_name}: хеш подписи не совпадает с подписанными байтами")


def test_sequential(source: bytes, failures: list[str]) -> None:
    config = network.SessionConfig(mode=network.MODE_SEQUENTIAL, visible=False, doc_name="test.pdf")
    session = network.SignSession(source, config)
    server = network.SessionServer(session, port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        code = session.code

        # wrong code rejected
        bad = network.SessionClient(base, "ZZZZZZ")
        try:
            bad.info()
            failures.append("неверный код должен быть отклонён")
        except network.NetworkError:
            pass

        # web status page renders
        import urllib.request
        page = urllib.request.urlopen(base, timeout=5).read().decode("utf-8")
        if "сетевая сессия" not in page.lower():
            failures.append("веб-страница статусов не отрисовалась")

        first = network.SessionClient(base, code)
        first.info()
        first.join(StubCert("Первый Подписант", "AA11"))
        signed_one = first.prepare()
        if not signed_one.startswith(b"%PDF-"):
            failures.append("подготовленные байты должны начинаться с заголовка PDF")
        first.commit(make_test_cms(signed_one))
        state = first.state()
        if state.get("doc_version") != 2:
            failures.append(f"после первой подписи версия должна быть 2, получена {state.get('doc_version')}")
        statuses = {p["id"]: p["status"] for p in state.get("participants") or []}
        if statuses.get(first.participant_id) != network.STATUS_SIGNED:
            failures.append("статус первого участника должен быть signed")

        # duplicate certificate cannot join twice
        dupe = network.SessionClient(base, code)
        try:
            dupe.join(StubCert("Первый снова", "AA11"))
            failures.append("дублирующийся сертификат должен быть отклонён")
        except network.NetworkError:
            pass

        # second participant: queue busy while a reservation is held
        second = network.SessionClient(base, code)
        second.join(StubCert("Второй Подписант", "BB22"))
        reserved = second.prepare()
        third = network.SessionClient(base, code)
        third.join(StubCert("Третий Подписант", "CC33"))
        try:
            third.prepare()
            failures.append("prepare во время чужой резервации должен быть отклонён")
        except network.NetworkError as exc:
            if "Подождите" not in str(exc):
                failures.append(f"неожиданная ошибка очереди: {exc}")
        # digest mismatch discards the reservation and marks the participant
        try:
            second.commit(make_test_cms(b"soversenno drugie bajty"))
            failures.append("CMS с неверным хешем должен быть отклонён")
        except network.NetworkError:
            pass
        state = second.state()
        statuses = {p["id"]: p["status"] for p in state.get("participants") or []}
        if statuses.get(second.participant_id) != network.STATUS_ERROR:
            failures.append("после неверной подписи статус должен быть error")
        if state.get("doc_version") != 2:
            failures.append("неверная подпись не должна менять версию документа")

        # second signs correctly after the error
        fresh = second.prepare()
        second.commit(make_test_cms(fresh))
        state = second.state()
        if state.get("doc_version") != 3:
            failures.append(f"после второй подписи версия должна быть 3, получена {state.get('doc_version')}")

        # decline works and frees nothing
        third.decline("не согласен")
        state = third.state()
        statuses = {p["id"]: p["status"] for p in state.get("participants") or []}
        if statuses.get(third.participant_id) != network.STATUS_DECLINED:
            failures.append("статус должен быть declined")

        check_final_document(session.document_bytes(), 2, failures)
        document, sig_files, note = session.results()
        if sig_files:
            failures.append("в последовательном режиме .sig файлов быть не должно")
        if "2" not in note:
            failures.append(f"в итоговой заметке должно быть число подписей: {note}")
    finally:
        server.shutdown()


def test_parallel(source: bytes, failures: list[str]) -> None:
    config = network.SessionConfig(mode=network.MODE_PARALLEL, doc_name="test.pdf")
    session = network.SignSession(source, config)
    server = network.SessionServer(session, port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        one = network.SessionClient(base, session.code)
        one.join(StubCert("Первый Подписант", "AA11"))
        data, version = one.document()
        if data != source:
            failures.append("в параллельном режиме участник получает исходный файл")
        one.submit(version, make_test_cms(source))

        two = network.SessionClient(base, session.code)
        two.join(StubCert("Второй Подписант", "BB22"))
        data2, version2 = two.document()
        two.submit(version2, make_test_cms(data2))

        state = two.state()
        signed = [p for p in state.get("participants") or [] if p["status"] == network.STATUS_SIGNED]
        if len(signed) != 2:
            failures.append("должно быть 2 подписавших участника")

        document, sig_files, note = session.results()
        if document != source:
            failures.append("параллельный итог — исходный документ без изменений")
        if len(sig_files) != 2:
            failures.append(f"ожидалось 2 файла .sig, получено {len(sig_files)}")
        for name, cms in sig_files:
            if not name.endswith(".pdf.sig"):
                failures.append(f"имя файла подписи должно оканчиваться на .pdf.sig: {name}")
            if "_" not in name:
                failures.append(f"в имени подписи должен быть участник: {name}")
            digest = crypto.cms_message_digest(cms)
            actual = crypto.streebog256(source)
            if bytes(digest) != bytes(actual):
                failures.append(f"{name}: хеш .sig не соответствует исходному документу")

        # финальный архив: документ + .sig каждого + протокол
        import io
        import zipfile

        archive = session.package_zip()
        if not archive.startswith(b"PK"):
            failures.append("пакет должен быть zip-архивом")
        names = zipfile.ZipFile(io.BytesIO(archive)).namelist()
        expected_doc = "test.pdf"
        if expected_doc not in names:
            failures.append(f"в архиве нет исходного документа: {names}")
        sig_entries = [n for n in names if n.endswith(".pdf.sig")]
        if len(sig_entries) != 2:
            failures.append(f"в архиве должно быть 2 .sig, найдено {sig_entries}")
        if "Protokol.pdf" not in names:
            failures.append(f"в архиве нет протокола: {names}")
        else:
            protocol_bytes = zipfile.ZipFile(io.BytesIO(archive)).read("Protokol.pdf")
            if not protocol_bytes.startswith(b"%PDF"):
                failures.append("Protokol.pdf не является PDF")
        # архив отдаётся участнику по HTTP
        name, downloaded = two.package()
        if not downloaded.startswith(b"PK"):
            failures.append("клиент не получил архив")
        if not name.endswith(".zip"):
            failures.append(f"неверное имя архива: {name}")
    finally:
        server.shutdown()


def test_strict_order(source: bytes, failures: list[str]) -> None:
    config = network.SessionConfig(
        mode=network.MODE_SEQUENTIAL, strict_order=True, visible=False, doc_name="test.pdf"
    )
    session = network.SignSession(source, config)
    server = network.SessionServer(session, port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        one = network.SessionClient(base, session.code)
        one.join(StubCert("Первый", "AA11"))
        two = network.SessionClient(base, session.code)
        two.join(StubCert("Второй", "BB22"))
        try:
            two.prepare()
            failures.append("строгая очередь: второй не должен подписывать первым")
        except network.NetworkError as exc:
            if "очередь" not in str(exc).lower() and "Подписывает" not in str(exc):
                failures.append(f"неожиданный отказ строгой очереди: {exc}")
        signed_one = one.prepare()
        one.commit(make_test_cms(signed_one))
        signed_two = two.prepare()
        two.commit(make_test_cms(signed_two))
        check_final_document(session.document_bytes(), 2, failures)
    finally:
        server.shutdown()


def test_helpers(failures: list[str]) -> None:
    code = network._new_code()
    if not code.isdigit() or len(code) != 6:
        failures.append(f"код сессии должен быть 6 цифр, получен {code!r}")

    cases = {
        "192.168.1.10": "http://192.168.1.10:8443",
        "192.168.1.10:9000": "http://192.168.1.10:9000",
        "http://host": "http://host:8443",
        "https://host:443": "https://host:443",
        "host": "http://host:8443",
    }
    for raw, expected in cases.items():
        actual = network.normalize_base_url(raw)
        if actual != expected:
            failures.append(f"адрес {raw!r}: ожидался {expected!r}, получен {actual!r}")

    for bad in ("", "   "):
        try:
            network.normalize_base_url(bad)
            failures.append(f"пустой адрес {bad!r} должен отклоняться")
        except network.NetworkError:
            pass

    client = network.SessionClient("127.0.0.1", "123456")
    if client.base_url != "http://127.0.0.1:8443":
        failures.append(f"клиент не подставил порт: {client.base_url}")


def test_order(source: bytes, failures: list[str]) -> None:
    """Sequential mode: the host may change the signing order."""
    config = network.SessionConfig(
        mode=network.MODE_SEQUENTIAL, strict_order=True, visible=False, doc_name="test.pdf"
    )
    session = network.SignSession(source, config)
    parts = [
        session.join({"name": name, "thumbprint": thumb})[0]
        for name, thumb in (("Первый", "AA"), ("Второй", "BB"), ("Третий", "CC"))
    ]
    pids = [p.pid for p in parts]

    session.reorder_participants([pids[2], pids[0], pids[1]])
    order = [(p.order, p.name) for p in sorted(session._participants, key=lambda p: p.order)]
    if order != [(1, "Третий"), (2, "Первый"), (3, "Второй")]:
        failures.append(f"порядок после перестановки неверен: {order}")

    try:
        session.prepare_signature(parts[0].token)
        failures.append("строгая очередь не учла новый порядок")
    except network.NetworkError:
        pass
    signed_bytes, _version = session.prepare_signature(parts[2].token)
    if not signed_bytes:
        failures.append("первый по новому порядку не смог подписать")
    session.cancel_pending(parts[2].token)

    if not session.move_participant(parts[1].pid, -1):
        failures.append("move_participant вверх не сработал")
    if session.move_participant(parts[1].pid, -5):
        failures.append("move_participant должен отклонять выход за границы")

    parallel = network.SignSession(
        source, network.SessionConfig(mode=network.MODE_PARALLEL, doc_name="test.pdf")
    )
    parallel_pids = [
        parallel.join({"name": name, "thumbprint": thumb})[0].pid
        for name, thumb in (("A", "AA"), ("B", "BB"))
    ]
    try:
        parallel.reorder_participants(parallel_pids)
        failures.append("в соподписи перестановка должна быть запрещена")
    except network.NetworkError:
        pass

    # a participant who already signed stays pinned at the front
    pinned = network.SignSession(
        source, network.SessionConfig(mode=network.MODE_SEQUENTIAL, doc_name="test.pdf")
    )
    pinned_parts = [
        pinned.join({"name": name, "thumbprint": thumb})[0]
        for name, thumb in (("X", "AA"), ("Y", "BB"), ("Z", "CC"))
    ]
    reserved, _version = pinned.prepare_signature(pinned_parts[0].token)
    pinned.commit_signature(pinned_parts[0].token, make_test_cms(reserved))
    pinned.reorder_participants([pinned_parts[2].pid, pinned_parts[1].pid, pinned_parts[0].pid])
    order = [(p.order, p.name, p.status) for p in sorted(pinned._participants, key=lambda p: p.order)]
    if order[0][2] != network.STATUS_SIGNED:
        failures.append(f"подписавший участник должен остаться первым: {order}")


def test_host_signing(source: bytes, failures: list[str]) -> None:
    """The organizer joins with its own certificate and signs the document."""
    import io
    import zipfile

    # parallel (соподпись): the organizer's .sig goes into the package
    session = network.SignSession(
        source, network.SessionConfig(mode=network.MODE_PARALLEL, doc_name="test.pdf")
    )
    host = session.join_host({
        "name": "Организатор Тестов",
        "organization": "ООО «Орг»",
        "thumbprint": "HOST1",
    })
    if not host.is_host:
        failures.append("организатор должен помечаться is_host")
    public = session.snapshot()["participants"]
    if not public or not public[0].get("is_host"):
        failures.append("в состоянии сессии нет признака организатора")
    if session.host_pid != host.pid:
        failures.append("host_pid не совпадает с участником-организатором")
    again = session.join_host({"name": "Повтор", "thumbprint": "HOST1"})
    if again.pid != host.pid:
        failures.append("повторный вход организатора должен возвращать того же участника")

    session.submit_signature(host.token, session.version, make_test_cms(source))
    document, files, _note = session.results()
    if document != source:
        failures.append("соподпись: документ не должен меняться")
    if len(files) != 1 or "Организатор" not in files[0][0]:
        failures.append(f"подпись организатора не попала в .sig: {files}")
    names = zipfile.ZipFile(io.BytesIO(session.package_zip())).namelist()
    if not any("Организатор" in name and name.endswith(".pdf.sig") for name in names):
        failures.append(f"в архиве нет .sig организатора: {names}")
    page = network.render_status_page(session.snapshot(), "127.0.0.1:8443")
    if "организатор" not in page:
        failures.append("веб-страница статусов не помечает организатора")
    try:
        session.join({"name": "Дубль", "thumbprint": "HOST1"})
        failures.append("сертификат организатора не должен пускать второго участника")
    except network.NetworkError:
        pass

    # sequential: the organizer joins at the end and signs a revision
    seq = network.SignSession(
        source, network.SessionConfig(mode=network.MODE_SEQUENTIAL, doc_name="test.pdf")
    )
    participant = seq.join({"name": "Участник", "thumbprint": "P1"})[0]
    host2 = seq.join_host({"name": "Организатор", "thumbprint": "H1"})
    if host2.order != 2:
        failures.append(f"организатор должен встать в конец очереди: {host2.order}")
    signed_bytes, _version = seq.prepare_signature(host2.token)
    seq.commit_signature(host2.token, make_test_cms(signed_bytes))
    check_final_document(seq.document_bytes(), 1, failures)
    seq_snapshot = seq.snapshot()
    if not any(p["is_host"] for p in seq_snapshot["participants"]):
        failures.append("организатор потерялся в состоянии последовательной сессии")

    # strict order: the organizer joined last waits for the first participant
    strict = network.SignSession(
        source,
        network.SessionConfig(
            mode=network.MODE_SEQUENTIAL, strict_order=True, doc_name="test.pdf"
        ),
    )
    first = strict.join({"name": "Первый", "thumbprint": "S1"})[0]
    boss = strict.join_host({"name": "Организатор", "thumbprint": "SH"})
    try:
        strict.prepare_signature(boss.token)
        failures.append("строгая очередь: организатор не должен подписывать раньше первого")
    except network.NetworkError:
        pass
    signed_first, _version = strict.prepare_signature(first.token)
    strict.commit_signature(first.token, make_test_cms(signed_first))
    signed_boss, _version = strict.prepare_signature(boss.token)
    strict.commit_signature(boss.token, make_test_cms(signed_boss))
    check_final_document(strict.document_bytes(), 2, failures)

    # a finished session does not accept the organizer
    finished = network.SignSession(source, network.SessionConfig(doc_name="test.pdf"))
    finished.finalize("done")
    try:
        finished.join_host({"name": "Поздно", "thumbprint": "L"})
        failures.append("завершённая сессия не должна принимать организатора")
    except network.NetworkError:
        pass


def main() -> int:
    source = find_fixture().read_bytes()
    failures: list[str] = []
    test_helpers(failures)
    test_sequential(source, failures)
    test_parallel(source, failures)
    test_strict_order(source, failures)
    test_order(source, failures)
    test_host_signing(source, failures)
    if failures:
        print("FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("OK: последовательный и параллельный режимы, очередь, отказы, .sig, подпись организатора")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
