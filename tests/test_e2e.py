import io
import re
import shutil
import zipfile
from pathlib import Path

from cai_docs.cli import main as cli_main
from cai_docs.config import Config
from cai_docs.pipeline import run

FIX = Path(__file__).parent / "fixtures"


def _subprocess_envelope(name: str, guid: str) -> bytes:
    return f"""<aetgt:getResponse xmlns:aetgt="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd"
                   xmlns:types1="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd">
   <types1:Item>
      <types1:EntryId>{name}::pd.xml</types1:EntryId>
      <types1:Name>{name}</types1:Name>
      <types1:MimeType>application/xml+process</types1:MimeType>
      <types1:PublishedContributionId>project:/spi.{name}/{name}.pd.xml</types1:PublishedContributionId>
      <types1:Entry>
         <process xmlns="http://schemas.active-endpoints.com/appmodules/screenflow/2010/10/avosScreenflow.xsd"
                  GUID="{guid}" name="{name}" displayName="{name}">
            <input/><output/><tempFields/>
            <flow id="f1"><start id="s1"><link id="l1" targetId="e1"/></start>
            <end id="e1"><title>End</title></end></flow>
         </process>
      </types1:Entry>
      <types1:GUID>{guid}</types1:GUID>
      <types1:DisplayName>{name}</types1:DisplayName>
   </types1:Item>
</aetgt:getResponse>""".encode()


def _make_input_zip(tmp_path: Path) -> Path:
    src = tmp_path / "export"
    (src / "processes").mkdir(parents=True)
    (src / "subprocesses").mkdir(parents=True)
    (src / "misc").mkdir(parents=True)

    for stem in ("createMultipleIdentifier", "retrieveconsents"):
        shutil.copy(FIX / "real" / f"{stem}.PROCESS.xml", src / "processes")

    # subprocesses both real processes call (shared GUIDs) -> must resolve
    for name, guid in (
        ("GetConfiguration", "2Zf1IBmmkiQcZh0NmtUImO"),
        ("GetMDMIDProcess", "0sK8k757Eikl8JFY8ldHIX"),
        ("GetCollectionPoint", "8ETedhAycxaeWe1diMeYO4"),
        ("BuildLocalizedId", "6WAjTPVYl4Pi3XNAg2ZvIo"),
    ):
        (src / "subprocesses" / f"{name}.PROCESS.xml").write_bytes(
            _subprocess_envelope(name, guid)
        )

    for fx in ("MyServiceConnector.SERVICECONNECTOR.xml",
               "OT-Submit-Consent.CONNECTION.xml", "weird_unknown.xml"):
        shutil.copy(FIX / "synthetic" / fx, src / "misc")

    # nested per-asset zip (Informatica nests these)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr(
            "ConsentRecord.PROCESSOBJECT.xml",
            (FIX / "synthetic" / "ConsentRecord.PROCESSOBJECT.xml").read_bytes(),
        )
    (src / "misc" / "nested_asset.zip").write_bytes(inner.getvalue())

    zip_path = tmp_path / "cai_export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return zip_path


def test_end_to_end_from_zip(tmp_path):
    zip_path = _make_input_zip(tmp_path)
    vault = tmp_path / "vault"
    cfg = Config(input_path=zip_path, output_dir=vault, cache_dir=tmp_path / "c",
                 use_llm=False)
    report = run(cfg)

    # report sanity
    assert report.counts_by_type.get("process", 0) >= 6
    assert report.counts_by_type.get("connection", 0) == 1
    assert report.counts_by_type.get("unknown", 0) == 1
    assert report.total_assets >= 9

    pages = {p.stem: p for p in vault.rglob("*.md")}
    assert "Home.md".removesuffix(".md") in pages
    assert (vault / "_MOC Processes.md").exists()

    # one page per asset incl. nested-zip-extracted process object
    for stem in ("createMultipleIdentifier", "retrieveconsents",
                 "GetConfiguration", "BuildLocalizedId", "ConsentRecord"):
        assert stem in pages, stem

    # every process page has a mermaid flow
    for stem in ("createMultipleIdentifier", "retrieveconsents"):
        assert "```mermaid" in pages[stem].read_text("utf-8")

    # shared subprocess GUIDs resolved: both processes link the SAME notes
    create = pages["createMultipleIdentifier"].read_text("utf-8")
    retrieve = pages["retrieveconsents"].read_text("utf-8")
    assert "[[GetConfiguration]]" in create and "[[GetConfiguration]]" in retrieve
    assert "[[BuildLocalizedId]]" in create and "[[BuildLocalizedId]]" in retrieve

    # embedded SQL surfaced
    assert "INSERT INTO integration_layer.consent_data_events" in create

    # unknown asset flagged
    assert "needs-review" in pages["weird_unknown"].read_text("utf-8")

    # no dangling wikilinks (ignore fenced mermaid blocks)
    note_names = set(pages)
    fence = re.compile(r"```.*?```", re.DOTALL)
    link = re.compile(r"\[\[([^\]]+)\]\]")
    for p in pages.values():
        text = fence.sub("", p.read_text("utf-8"))
        for m in link.finditer(text):
            tgt = m.group(1).split("|")[0].split("#")[0].strip()
            assert tgt in note_names, f"dangling [[{tgt}]] in {p.name}"

    # no PII leaked (sample-data redacted by default)
    assert "kjsdhjgs1009" not in create


def test_cli_smoke(tmp_path, capsys):
    zip_path = _make_input_zip(tmp_path)
    vault = tmp_path / "cli_vault"
    rc = cli_main([str(zip_path), "-o", str(vault), "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cai-docs run report" in out
    assert (vault / "Home.md").exists()
