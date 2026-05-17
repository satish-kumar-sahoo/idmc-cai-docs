from cai_docs.classify import (
    CONNECTION,
    GUIDE,
    PROCESS,
    PROCESS_OBJECT,
    SCHEMA,
    SERVICE_CONNECTOR,
    UNKNOWN,
    classify,
)
from cai_docs.xmlmodel import parse


def test_real_processes_classify_high_confidence(real_create, real_retrieve):
    for raw in (real_create, real_retrieve):
        at, conf, signals = classify(parse(raw))
        assert at == PROCESS, signals
        assert conf >= 0.9, (conf, signals)


def test_synthetic_types(synthetic_dir, raw_loader):
    cases = {
        "MyServiceConnector.SERVICECONNECTOR.xml": SERVICE_CONNECTOR,
        "OT-Submit-Consent.CONNECTION.xml": CONNECTION,
        "ConsentRecord.PROCESSOBJECT.xml": PROCESS_OBJECT,
        "ConsentGuide.GUIDE.xml": GUIDE,
        "purposes.xsd": SCHEMA,
    }
    for fname, expected in cases.items():
        at, conf, signals = classify(parse(raw_loader(synthetic_dir / fname)))
        assert at == expected, (fname, at, signals)
        assert conf > 0.45, (fname, conf, signals)


def test_unknown_is_flagged(synthetic_dir, raw_loader):
    at, conf, signals = classify(parse(raw_loader(synthetic_dir / "weird_unknown.xml")))
    assert at == UNKNOWN
    assert conf == 0.0


def test_processobject_not_misread_as_process(synthetic_dir, raw_loader):
    at, _, signals = classify(
        parse(raw_loader(synthetic_dir / "ConsentRecord.PROCESSOBJECT.xml"))
    )
    assert at == PROCESS_OBJECT, signals
